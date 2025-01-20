import base64
import subprocess
import traceback
from flask import Flask, jsonify, render_template, request, send_file
from flask_pymongo import PyMongo
from werkzeug.utils import secure_filename
import os
import re
import zipfile
import io
from PIL import Image, ImageDraw, ImageFont
import qrcode
from bson import ObjectId
from flask_cors import CORS

app = Flask(__name__)

app.config["MONGO_URI"] = "mongodb://mongo:27017/mydatabase"
app.config["ALLOWED_EXTENSIONS"] = {"ifc"}
app.config["UPLOAD_FOLDER"] = "/app/uploads/"
app.config["TEMP_DIR"] = "/app/"
app.config["IFC_CONVERT_FOLDER"] = "/app/IfcConvert/"
mongo = PyMongo(app)
CORS(app)
files_collection = mongo.db.files
qr_collection = mongo.db.qrcodes


def allowed_file(filename):
    """
    Check if a file has an allowed extension based on the filename.

    Parameters:
    filename (str): The name of the file to check.

    Returns:
    bool: True if the file has an allowed extension, False otherwise.
    """
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


def convert_file_to_dae(filename):
    """
    Convert an IFC file to DAE format using IfcConvert.

    This function takes an IFC filename, converts it to DAE format using the IfcConvert tool,
    and moves the resulting DAE file to the upload folder.

    Parameters:
    filename (str): The name of the IFC file to convert, without the .ifc extension.

    Returns:
    tuple: A tuple containing three elements:
        - str: The path to the generated DAE file.
        - str: The standard output of the conversion process.
        - str: The standard error of the conversion process.

    Raises:
    FileNotFoundError: If the IfcConvert executable or the IFC file is not found.
    PermissionError: If there's no write permission for the upload folder.
    RuntimeError: If an error occurs during the DAE conversion process.
    Exception: For any other unexpected errors.
    """
    exe_path = os.path.join(os.getcwd(), app.config["IFC_CONVERT_FOLDER"], "IfcConvert")
    ifc_file_path = os.path.join(
        os.getcwd(), app.config["UPLOAD_FOLDER"], filename + ".ifc"
    )
    dae_file_path = os.path.join(os.getcwd(), app.config["TEMP_DIR"], filename + ".dae")

    if not os.path.exists(exe_path):
        raise FileNotFoundError("IfcConvert.exe nebyl nalezen.")

    if not os.path.exists(ifc_file_path):
        raise FileNotFoundError("IFC soubor nebyl nalezen.")

    if not os.access(app.config["UPLOAD_FOLDER"], os.W_OK):
        raise PermissionError(
            f"Nemáte oprávnění zapisovat do: {app.config['UPLOAD_FOLDER']}"
        )

    try:
        result = subprocess.run(
            [exe_path, ifc_file_path, dae_file_path, "--use-element-names"],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(["mv", dae_file_path, app.config["UPLOAD_FOLDER"]])
        return dae_file_path, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Došlo k chybě při DAE konverzi: {ifc_file_path} -> {dae_file_path} {e.stderr}"
        )
    except Exception as e:
        raise


def convert_dae_to_obj(dae_filename):
    """
    Convert a DAE (COLLADA) file to OBJ format using Blender.

    This function takes a DAE filename, uses Blender to import the DAE file,
    and then exports it as an OBJ file. The conversion is done in the background
    without opening the Blender GUI.

    Parameters:
    dae_filename (str): The name of the DAE file to convert, including the .dae extension.

    Returns:
    str: The file path of the generated OBJ file.

    Raises:
    RuntimeError: If the OBJ file is not created or if there's an error during the conversion process.
    Exception: For any other unexpected errors during the conversion process.
    """
    try:
        obj_filename = dae_filename.replace(".dae", ".obj")
        dae_file_path = os.path.join(app.config["UPLOAD_FOLDER"], dae_filename)
        obj_file_path = os.path.join(app.config["UPLOAD_FOLDER"], obj_filename)

        blender_command = [
            "blender",
            "--background",
            "--python-expr",
            f"import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); bpy.ops.wm.collada_import(filepath='{dae_file_path}'); bpy.ops.export_scene.obj(filepath='{obj_file_path}')",
        ]

        result = subprocess.run(
            blender_command, capture_output=True, text=True, check=True
        )

        if not os.path.exists(obj_file_path):
            raise RuntimeError(f"OBJ soubor nebyl vytvořen na: {obj_file_path}")

        return obj_file_path
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Došlo k chybě při konverzi na OBJ: {e.stderr}")
    except Exception as e:
        raise


def convert_objectid_to_str(file):
    """
    Convert the ObjectId of a file document to a string.

    This function takes a file document (typically from MongoDB) and converts
    its '_id' field from an ObjectId to a string. This is often necessary
    when preparing documents for JSON serialization.

    Parameters:
    file (dict): A dictionary representing a file document, containing an '_id' field.

    Returns:
    dict: The same file document with its '_id' field converted to a string.
    """
    file["_id"] = str(file["_id"])
    return file


def inject_ids_into_obj(obj_file_path):
    """
    Add unique IDs to object names in an OBJ file.
    """
    id_counter = 1
    updated_lines = []

    with open(obj_file_path, "r") as file:
        lines = file.readlines()

    for line in lines:
        if line.startswith("o "):
            object_name = line.strip().split(" ")[1]
            new_object_name = f"{object_name}:_ID{id_counter}_:"
            updated_lines.append(f"o {new_object_name}\n")
            id_counter += 1
        else:
            updated_lines.append(line)

    with open(obj_file_path, "w") as file:
        file.writelines(updated_lines)


def update_obj_file(file_path, updates):
    """
    Update the names of objects in an OBJ file based on given updates.

    This function reads an OBJ file specified by `file_path`, iterates through the updates,
    and modifies the names of objects in the file based on the provided updates. The updates
    are specified as a list of dictionaries, where each dictionary contains an 'id' and a 'name'.
    The function searches for objects in the OBJ file with matching IDs and replaces their names
    with the new names provided in the updates.

    Parameters:
    file_path (str): The path to the OBJ file to be updated.
    updates (list): A list of dictionaries, where each dictionary contains an 'id' and a 'name'.
        The 'id' represents the ID of the object to be updated, and the 'name' represents the
        new name for the object.

    Returns:
    None. The function modifies the OBJ file in-place.
    """
    with open(file_path, "r") as file:
        lines = file.readlines()

    for update in updates:
        model_id = f":_ID{update['id']}_:"
        new_name = update["name"]

        for i, line in enumerate(lines):
            if line.startswith("o") and model_id in line:
                lines[i] = f"o {new_name}\n"
                break

    with open(file_path, "w") as file:
        file.writelines(lines)


@app.route("/", methods=["GET", "POST"])
def index():
    return render_template("index.html")


@app.route("/files", methods=["GET"])
def get_files():
    files = files_collection.find()
    file_list = [convert_objectid_to_str(file) for file in files]
    return jsonify(file_list), 200


@app.route("/files", methods=["POST"])
def upload():
    uploaded_file = request.files.get("file")
    if not uploaded_file or uploaded_file.filename == "":
        return jsonify({"error": "Nebyl vybrán žádný soubor"}), 400
    if not allowed_file(uploaded_file.filename):
        return (
            jsonify(
                {"error": "Neplatný typ souboru. Jsou povoleny pouze .ifc soubory."}
            ),
            400,
        )

    safe_filename = secure_filename(uploaded_file.filename)
    file_metadata = {
        "filename": safe_filename.rsplit(".", 1)[0],
    }
    file_record = files_collection.insert_one(file_metadata)
    file_metadata["_id"] = str(file_record.inserted_id)

    file_path = os.path.join(app.config["UPLOAD_FOLDER"], file_metadata["_id"] + ".ifc")
    if os.path.exists(file_path):
        return jsonify({"error": "Soubor již existuje"}), 400
    uploaded_file.save(file_path)

    try:
        dae_file_path, conversion_output, conversion_error = convert_file_to_dae(
            file_metadata["_id"]
        )
        obj_file_path = convert_dae_to_obj(os.path.basename(dae_file_path))

        inject_ids_into_obj(obj_file_path)

        # Return the response without storing file paths in the database
        return (
            jsonify(
                {
                    "message": "Soubor byl úspěšně nahrán a převeden!",
                    "file": file_metadata,
                    "dae_conversion_output": conversion_output,
                    "dae_conversion_error": conversion_error,
                    "obj_file_path": obj_file_path,
                }
            ),
            201,
        )
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/files/<file_id>", methods=["PUT"])
def edit_file(file_id):
    data = request.get_json()
    updates = data.get("updates", [])

    obj_file_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{file_id}.obj")

    update_obj_file(obj_file_path, updates)

    return jsonify({"status": "success", "file_id": file_id}), 200


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
    return jsonify({"error": "Soubor nebyl nalezen"}), 404


@app.route("/files/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    file_record = files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_record:
        return jsonify({"error": "Soubor nebyl nalezen"}), 404
    file_paths = [
        os.path.join(app.config["UPLOAD_FOLDER"], f"{file_id}.ifc"),
        os.path.join(app.config["UPLOAD_FOLDER"], f"{file_id}.dae"),
        os.path.join(app.config["UPLOAD_FOLDER"], f"{file_id}.obj"),
    ]
    for file_path in file_paths:
        if os.path.exists(file_path):
            os.remove(file_path)
    files_collection.delete_one({"_id": ObjectId(file_id)})

    return jsonify({"success": "Soubor a metadata byly úspěšně odstraněny!"}), 200


@app.route("/files/<file_id>/download/<extension>", methods=["GET"])
def download(file_id, extension):
    valid_extensions = ["obj", "dae", "ifc"]
    if extension not in valid_extensions:
        return (
            jsonify(
                {
                    "error": f"Neplatná přípona. Jsou povoleny pouze {', '.join(valid_extensions)}."
                }
            ),
            400,
        )
    file = files_collection.find_one({"_id": ObjectId(file_id)})
    if file:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{file_id}.{extension}")
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
        else:
            return (
                jsonify({"error": f"{extension.upper()} soubor nebyl nalezen."}),
                404,
            )
    else:
        return jsonify({"error": "Soubor nebyl nalezen."}), 404


@app.route("/qrcodes/<file_id>", methods=["GET"])
def get_qrcodes_by_file(file_id):

    try:
        qr_codes = qr_collection.find({"file_id": file_id})
        qr_codes_list = list(qr_codes)

        if not qr_codes_list:
            return jsonify({"error": "No QR codes found for this file."}), 404
        qr_codes_list = [convert_objectid_to_str(_id) for _id in qr_codes_list]

        return jsonify(qr_codes_list), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/qrcodes", methods=["POST"])
def upload_qr():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Missing JSON data"}), 400

        x = data.get("x")
        y = data.get("y")
        z = data.get("z")
        yrot = data.get("yrot")
        room = data.get("room", "Default Room")
        building = data.get("building", "Default Building")
        file_id = str(data.get("file_id"))
        file_url = data.get("file_url")

        if not all([x, y, z, yrot, file_id]):
            return jsonify({"error": "Missing required parameters"}), 400
        qr_metadata = {
            "building": building,
            "room": room,
            "x": x,
            "y": y,
            "z": z,
            "yrot": yrot,
            "file_id": file_id,
            "file_url": file_url,
        }
        qr_record = qr_collection.insert_one(qr_metadata)
        qr_metadata["_id"] = str(qr_record.inserted_id)

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )

        data_string = f"{building};{room};{int(x * 100)};{int(y * 100)};{int(z * 100)};{int(yrot * 100)};{file_url}"
        qr.add_data(base64.b64encode(data_string.encode()).decode())
        qr.make(fit=True)

        qr_image = qr.make_image(fill_color="black", back_color="white")
        qr_image = qr_image.convert("RGB")

        draw = ImageDraw.Draw(qr_image)
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40
        )

        building_name = building if building else "Default"
        room_name = room if room else "Default"

        header_text = f"{building_name}\n{room_name}"

        qr_width, qr_height = qr_image.size
        text_bbox = draw.textbbox((0, 0), header_text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        top_margin = text_height + 30

        total_height = top_margin + qr_height
        output_image = Image.new("RGB", (qr_width, total_height), color="white")

        text_x = (qr_width - text_width) // 2
        text_y = 20

        draw = ImageDraw.Draw(output_image)
        draw.text((text_x, text_y), building_name, font=font, fill="black")
        draw.text((text_x, text_y + 40), room_name, font=font, fill="black")
        output_image.paste(qr_image, (0, top_margin))

        qr_image_path = os.path.join(
            app.config["UPLOAD_FOLDER"], f"{qr_metadata['_id']}.png"
        )
        output_image.save(qr_image_path)

        return (
            jsonify(
                {
                    "message": "QR code generated successfully",
                    "qr_metadata": qr_metadata,
                    "qr_image_url": f"/uploads/{qr_metadata['_id']}.png",
                }
            ),
            201,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/qrcodes/<qr_id>/download", methods=["GET"])
def get_qrcode(qr_id):
    try:
        qr_image_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{qr_id}.png")
        if not os.path.exists(qr_image_path):
            return jsonify({"error": "QR code not found"}), 404
        return send_file(qr_image_path, mimetype="image/png")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/qrcodes/<file_id>/downloadall", methods=["GET"])
def get_qrcodes_by_file_id(file_id):
    try:
        qr_codes = qr_collection.find({"file_id": file_id})
        if not qr_codes:
            return jsonify({"error": "No QR codes found for the given file_id"}), 404
        zip_stream = io.BytesIO()
        with zipfile.ZipFile(zip_stream, "w", zipfile.ZIP_DEFLATED) as zipf:
            for qr_metadata in qr_codes:
                qr_id = qr_metadata["_id"]
                qr_image_path = os.path.join(
                    app.config["UPLOAD_FOLDER"], f"{qr_id}.png"
                )

                if os.path.exists(qr_image_path):
                    with open(qr_image_path, "rb") as qr_image_file:
                        zipf.writestr(f"{qr_id}.png", qr_image_file.read())
                else:
                    continue
        zip_stream.seek(0)
        return send_file(
            zip_stream,
            as_attachment=True,
            download_name="qrcodes.zip",
            mimetype="application/zip",
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/qrcodes/<qr_id>", methods=["DELETE"])
def delete_qrcode(qr_id):
    try:
        qr_record = qr_collection.find_one({"_id": ObjectId(qr_id)})
        if qr_record:
            qr_image_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{qr_id}.png")
            if os.path.exists(qr_image_path):
                os.remove(qr_image_path)
            qr_collection.delete_one({"_id": ObjectId(qr_id)})
            return jsonify({"success": "QR code deleted successfully"}), 200
        else:
            return jsonify({"error": "QR code not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
