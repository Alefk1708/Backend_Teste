import cloudinary.uploader
import uuid

def upload_avatar(file, folder: str):
    result = cloudinary.uploader.upload(
        file,
        folder=folder,
        public_id=str(uuid.uuid4()),
        overwrite=True
    )

    return { 
        "url": result["secure_url"],
        "public_id": result["public_id"]
    }