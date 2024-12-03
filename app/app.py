import subprocess
import logging
from flask import Flask, jsonify, render_template, request, send_file
from flask_pymongo import PyMongo
from werkzeug.utils import secure_filename
import os
from bson import ObjectId
import bpy

app = Flask(__name__)

app.config["MONGO_URI"] = "mongodb://mongo:27017/mydatabase"
app.config["ALLOWED_EXTENSIONS"] = {"ifc"}
app.config["UPLOAD_FOLDER"] = "/app/uploads/"
app.config["TEMP_DIR"] = "/app/"
app.config["IFC_CONVERT_FOLDER"] = "/app/IfcConvert/"

mongo = PyMongo(app)
files_collection = mongo.db.files

# Set up basic logging
logging.basicConfig(level=logging.DEBUG)


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


def convert_file_to_dae(filename):
    exe_path = os.path.join(os.getcwd(), app.config["IFC_CONVERT_FOLDER"], "IfcConvert")
    ifc_file_path = os.path.join(
        os.getcwd(), app.config["UPLOAD_FOLDER"], filename + ".ifc"
    )
    dae_file_path = os.path.join(os.getcwd(), app.config["TEMP_DIR"], filename + ".dae")

    # Debug: log paths
    logging.debug(f"Exe Path: {exe_path}")
    logging.debug(f"IFC File Path: {ifc_file_path}")
    logging.debug(f"DAE File Path: {dae_file_path}")

    if not os.path.exists(exe_path):
        raise FileNotFoundError("IfcConvert.exe not found.")

    if not os.path.exists(ifc_file_path):
        raise FileNotFoundError("IFC file not found.")

    try:
        # Debug: checking if we have write permissions in the upload folder
        if not os.access(app.config["UPLOAD_FOLDER"], os.W_OK):
            logging.error(
                f"Permission denied for writing to: {app.config['UPLOAD_FOLDER']}"
            )
            raise PermissionError(
                f"Permission denied for writing to: {app.config['UPLOAD_FOLDER']}"
            )

        result = subprocess.run(
            [exe_path, ifc_file_path, dae_file_path, "--use-element-names"],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(["mv", dae_file_path, app.config["UPLOAD_FOLDER"]])

        # Debug: log stdout and stderr from the conversion process
        logging.debug(f"DAE Conversion STDOUT: {result.stdout}")
        logging.debug(f"DAE Conversion STDERR: {result.stderr}")

        return dae_file_path, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        logging.error(f"Error during DAE conversion: {e.stderr}")
        raise RuntimeError(
            f"An error occurred during DAE conversion: {ifc_file_path} -> {dae_file_path} {e.stderr}"
        )
    except Exception as e:
        logging.error(f"Unexpected error during DAE conversion: {str(e)}")
        raise


def convert_dae_to_obj(dae_filename):
    """Converts a DAE file to OBJ using Blender in headless mode."""
    try:
        obj_filename = dae_filename.replace(".dae", ".obj")
        dae_file_path = os.path.join(app.config["UPLOAD_FOLDER"], dae_filename)
        obj_file_path = os.path.join(app.config["UPLOAD_FOLDER"], obj_filename)

        # Log file paths
        logging.debug(f"DAE File Path: {dae_file_path}")
        logging.debug(f"OBJ File Path: {obj_file_path}")

        # Run Blender in headless mode to convert the DAE to OBJ
        blender_command = [
            "blender",
            "--background",
            "--python-expr",
            f"import bpy; bpy.ops.wm.collada_import(filepath='{dae_file_path}'); bpy.ops.export_scene.obj(filepath='{obj_file_path}')",
        ]

        # Run the Blender command and capture its output
        result = subprocess.run(
            blender_command, capture_output=True, text=True, check=True
        )

        # Log stdout and stderr from the Blender process
        logging.debug(f"Blender STDOUT: {result.stdout}")
        logging.debug(f"Blender STDERR: {result.stderr}")

        # Check if the OBJ file was created
        if not os.path.exists(obj_file_path):
            raise RuntimeError(f"OBJ file not created at: {obj_file_path}")

        return obj_file_path
    except subprocess.CalledProcessError as e:
        logging.error(f"Error during OBJ conversion: {e.stderr}")
        raise RuntimeError(f"An error occurred during OBJ conversion: {e.stderr}")
    except Exception as e:
        logging.error(f"Unexpected error during OBJ conversion: {str(e)}")
        raise


def convert_objectid_to_str(file):
    file["_id"] = str(file["_id"])
    return file


@app.route("/", methods=["GET", "POST"])
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    uploaded_file = request.files.get("file")
    if not uploaded_file or uploaded_file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    if not allowed_file(uploaded_file.filename):
        return (
            jsonify({"error": "Invalid file type. Only .ifc files are allowed."}),
            400,
        )

    safe_filename = secure_filename(uploaded_file.filename)
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_filename)

    if os.path.exists(file_path):
        return jsonify({"error": "File already exists"}), 400

    uploaded_file.save(file_path)

    file_metadata = {
        "filename": safe_filename.rsplit(".", 1)[0],
    }
    file_record = files_collection.insert_one(file_metadata)
    file_metadata["_id"] = str(file_record.inserted_id)

    try:
        # Convert IFC to DAE
        dae_file_path, conversion_output, conversion_error = convert_file_to_dae(
            file_metadata["filename"]
        )

        # Convert DAE to OBJ
        obj_file_path = convert_dae_to_obj(os.path.basename(dae_file_path))

        # Update the file metadata in the database
        files_collection.update_one(
            {"_id": file_record.inserted_id},
            {
                "$set": {
                    "dae_file_path": dae_file_path,
                    "obj_file_path": obj_file_path,
                }
            },
        )

        return (
            jsonify(
                {
                    "message": "File uploaded and converted successfully!",
                    "file": file_metadata,
                    "dae_conversion_output": conversion_output,
                    "dae_conversion_error": conversion_error,
                    "obj_file_path": obj_file_path,
                }
            ),
            201,
        )
    except Exception as error:
        logging.error(f"Error during file upload and conversion: {str(error)}")
        return jsonify({"error": str(error)}), 500


@app.route("/files", methods=["GET"])
def get_files():
    files = files_collection.find()
    file_list = [convert_objectid_to_str(file) for file in files]
    return jsonify(file_list), 200


@app.route("/files/<file_id>", methods=["GET"])
def get_file(file_id):
    file_record = files_collection.find_one({"_id": ObjectId(file_id)})
    if file_record:
        file_record = convert_objectid_to_str(file_record)
        file_info = {
            "id": file_record["_id"],
            "filename": file_record["filename"],
        }
        return jsonify(file_info), 200
    return jsonify({"error": "File not found"}), 404


@app.route("/files/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    file_record = files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_record:
        return jsonify({"error": "File not found"}), 404
    filename = file_record["filename"]
    file_paths = [
        os.path.join(app.config["UPLOAD_FOLDER"], f"{filename}.ifc"),
        os.path.join(app.config["UPLOAD_FOLDER"], f"{filename}.dae"),
        os.path.join(app.config["UPLOAD_FOLDER"], f"{filename}.obj"),
    ]
    for file_path in file_paths:
        if os.path.exists(file_path):
            os.remove(file_path)
    files_collection.delete_one({"_id": ObjectId(file_id)})

    return jsonify({"success": "File and metadata deleted successfully!"}), 200


@app.route("/files/<id>/download/<extension>", methods=["GET"])
def download(id, extension):
    valid_extensions = ["obj", "dae", "ifc"]
    if extension not in valid_extensions:
        return (
            jsonify(
                {
                    "error": f"Invalid extension. Only {', '.join(valid_extensions)} are allowed."
                }
            ),
            400,
        )
    file = files_collection.find_one({"_id": ObjectId(id)})
    if file:
        filename = file.get("filename")
        if filename:
            file_path = os.path.join(
                app.config["UPLOAD_FOLDER"], f"{filename}.{extension}"
            )
            if os.path.exists(file_path):
                return send_file(file_path, as_attachment=True)
            else:
                return jsonify({"error": f"{extension.upper()} file not found."}), 404
        else:
            return jsonify({"error": "Filename not found in database."}), 404
    else:
        return jsonify({"error": "File not found."}), 404


@app.route("/dropdb")
def dropdb():
    files_collection.drop()
    return "Database dropped!"


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
