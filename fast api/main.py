
# from fastapi import FastAPI

# app = FastAPI()  # Must be defined before using it

# @app.get("/")
# async def root():
#     return {"message": "Hello World"}

# @app.get("/items/{item_id}")
# async def read_item(item_id: int):
#     return {"item_id": item_id}

import uvicorn
from fastapi import FastAPI, Path
from enum import Enum
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()  # Must be defined


# origins = [
#     "http://localhost:3000",
# ]


# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=origins,
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# class ModelName(str, Enum):
#     alexnet = "alexnet"
#     resnet = "resnet"
#     lenet = "lenet"

# # Path parameter with validation
# @app.get("/items/{item_id}")
# async def read_item(
#     item_id: int = Path(
#         title="Item ID",
#         description="The ID of the item",
#         gt=0,  # Greater than 0
#         le=1000  # Less than or equal to 1000
#     )
# ):
#     return {"item_id": item_id}

# # Enum path parameter
# @app.get("/models/{model_name}")
# async def get_model(model_name: ModelName):
#     if model_name == ModelName.alexnet:
#         return {"model": "alexnet", "layers": 5}
#     return {"model": model_name, "layers": 10}

# if __name__ == "__main__":
#     uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
# from pydantic import BaseModel, Field, validator
# from typing import Optional, List
# from datetime import datetime

# class Item(BaseModel):
#     id: Optional[int] = None
#     name: str = Field(
#         ...,
#         min_length=1,
#         max_length=100,
#         description="Item name"
#     )
#     price: float = Field(gt=0)
#     tax: Optional[float] = None
#     tags: List[str] = []
#     created_at: datetime = Field(default_factory=datetime.now)
    
#     @validator('tax')
#     def tax_positive(cls, v):
#         if v is not None and v < 0:
#             raise ValueError('Tax must be positive')
#         return v
    
#     class Config:
#         schema_extra = {
#             "example": {
#                 "name": "Laptop",
#                 "price": 999.99,
#                 "tax": 99.99
#             }
#         }
# from fastapi import Body, File, UploadFile, Form

# # Multiple body parameters
# @app.post("/create/")
# async def create(
#     item: Item,
#     user: User,
#     importance: int = Body(gt=0)
# ):
#     return {"item": item, "user": user}

# # Form data
# @app.post("/login/")
# async def login(
#     username: str = Form(),
#     password: str = Form()
# ):
#     return {"username": username}

# # File upload
# @app.post("/upload/")
# async def upload_file(
#     file: UploadFile,
#     description: Optional[str] = Form(None)
# ):
#     contents = await file.read()
#     return {
#         "filename": file.filename,
#         "size": len(contents)
#     }

# Path Parameter

# from fastapi import FastAPI

# app = FastAPI()

# @app.get("/users/{user_id}")
# def get_user(user_id: int):
#     return {"user_id": user_id}
# # Query Parameter

# from fastapi import FastAPI

# app = FastAPI()

# @app.get("/search")
# def search(q: str, limit: int = 10):
#     return {"query": q, "limit": limit}
# from fastapi import FastAPI
# from pydantic import BaseModel

# app = FastAPI()

# class Video(BaseModel):
#     title: str
#     duration: int

# @app.post("/videos")
# def upload_video(video: Video):
#     return {"status": "uploaded", "video": video}

# from fastapi import FastAPI, UploadFile, File
# import cv2
# import numpy as np
# import shutil
# import os

# app = FastAPI()

# # ✅ Add this (you forgot it)
# UPLOAD_FOLDER = "uploads"
# os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# def fake_violence_detection(frame):
#     gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
#     mean_intensity = np.mean(gray)

#     if mean_intensity < 50:
#         return True
#     return False


# @app.post("/fighting-video/")   # removed space & spelling fixed
# async def analyze_video(file: UploadFile = File(...)):

#     file_path = os.path.join(UPLOAD_FOLDER, file.filename)

#     # Save uploaded video
#     with open(file_path, "wb") as buffer:
#         shutil.copyfileobj(file.file, buffer)

#     cap = cv2.VideoCapture(file_path)

#     if not cap.isOpened():
#         return {"error": "Could not open video file"}

#     violence_detected = False
#     frame_count = 0

#     while True:
#         ret, frame = cap.read()
#         if not ret or frame is None:
#             break

#         frame_count += 1

#         if fake_violence_detection(frame):
#             violence_detected = True
#             break

#     cap.release()

#     return {
#         "filename": file.filename,
#         "frames_checked": frame_count,
#         "violence_detected": violence_detected
#     }

# main.py
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
import os

app = FastAPI()

# Make sure the 'videos' folder exists
os.makedirs("videos", exist_ok=True)

@app.post("/fighting-video/")
async def detect_violence(file: UploadFile = File(...)):
    # Save the uploaded video
    file_location = f"videos/{file.filename}"
    with open(file_location, "wb") as f:
        f.write(await file.read())

    # TODO: Add your video processing/AI model here
    violence_detected = False  # Example placeholder

    return JSONResponse({
        "filename": file.filename,
        "violence_detected": violence_detected,
        "message": "Video uploaded successfully"
    })


