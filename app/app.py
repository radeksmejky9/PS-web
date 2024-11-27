from flask import Flask, jsonify, render_template, request, send_file
from flask_pymongo import PyMongo
from werkzeug.utils import secure_filename
import subprocess
import os
from bson import ObjectId

app = Flask(__name__)

app.config["MONGO_URI"] = "mongodb://mongo:27017/mydatabase"
app.config["ALLOWED_EXTENSIONS"] = {"ifc"}
app.config["UPLOAD_FOLDER"] = "/app/uploads/"
app.config["IFC_CONVERT_FOLDER"] = "/app/IfcConvert/"

mongo = PyMongo(app)
files_collection = mongo.db.files


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


def convert_file(filename):
    exe_path = os.path.join(os.getcwd(), app.config["IFC_CONVERT_FOLDER"], "IfcConvert")
    ifc_file_path = os.path.join(os.getcwd(), app.config["UPLOAD_FOLDER"], filename)

    if not os.path.exists(exe_path):
        raise FileNotFoundError("IfcConvert.exe not found.")

    if not os.path.exists(ifc_file_path):
        raise FileNotFoundError("IFC file not found.")

    try:
        result = subprocess.run(
            [exe_path, ifc_file_path], capture_output=True, text=True, check=True
        )
        return result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"An error occurred: {e.stderr}")


def convert_objectid_to_str(file):
    file["_id"] = str(file["_id"])
    return file


@app.route("/", methods=["GET", "POST"])
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(file_path):
            return jsonify({"error": "File already exists"}), 400
        file.save(file_path)
        file_metadata = {
            "filename": filename,
            "path": file_path,
            "uploaded_at": subprocess.run(["date"], capture_output=True)
            .stdout.decode()
            .strip(),
        }
        file_record = files_collection.insert_one(file_metadata)
        file_metadata["_id"] = str(file_record.inserted_id)
        try:
            stdout, stderr = convert_file(filename)
            return (
                jsonify(
                    {
                        "message": "File uploaded and converted successfully!",
                        "file": file_metadata,
                        "conversion_output": stdout,
                        "conversion_error": stderr,
                    }
                ),
                201,
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        return (
            jsonify({"error": "Invalid file type. Only .ifc files are allowed."}),
            400,
        )


@app.route("/files", methods=["GET"])
def get_files():
    files = files_collection.find()
    file_list = [convert_objectid_to_str(file) for file in files]
    return jsonify(file_list), 200


@app.route("/files/<id>", methods=["GET"])
def get_file(id):
    file = files_collection.find_one({"_id": ObjectId(id)})
    if file:
        file = convert_objectid_to_str(file)
        file_data = {
            "id": file["_id"],
            "filename": file["filename"],
            "uploaded_at": file["uploaded_at"],
        }
        return jsonify(file_data), 200
    else:
        return jsonify({"error": "File not found"}), 404


@app.route("/files/<id>/download", methods=["GET"])
def download(id):
    file = files_collection.find_one({"_id": ObjectId(id)})
    if file:
        file_path = file["path"]
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
        else:
            return jsonify({"error": "File not found on disk"}), 404
    else:
        return jsonify({"error": "File not found"}), 404


@app.route("/dropdb")
def dropdb():
    files_collection.drop()
    return jsonify({"message": "Database dropped successfully!"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
