from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
# 使用 motor 进行异步 MongoDB 操作
from motor.motor_asyncio import AsyncIOMotorClient as MongoClient
from pymongo.server_api import ServerApi
from pymongo import ReturnDocument
from dotenv import load_dotenv
import os
from datetime import datetime
from typing import List, Optional, Dict, Any

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")
COLLECTION_NAME = os.getenv("COLLECTION_NAME")

if not MONGO_URI:
    raise ValueError("MONGO_URI environment variable is not set. Please set it in .env or your deployment environment.")
if not DATABASE_NAME:
    raise ValueError("DATABASE_NAME environment variable is not set. Please set it in .env or your deployment environment.")

app = FastAPI(
    title="Drift Bottle Core API",
    description="Core API for managing global drift bottle storage and picking status in MongoDB. User-specific picked bottle lists are managed client-side.",
    version="1.0.0"
)

client: Optional[MongoClient] = None
db: Optional[Any] = None
bottles_collection: Optional[Any] = None


class Image(BaseModel):
    """漂流瓶中图片的模型。"""
    type: str = Field(..., description="图片的类型。")
    data: str = Field(..., description="图片的URL地址。")

class BottleIn(BaseModel):
    """创建新漂流瓶的请求体模型。"""
    content: str = Field(..., description="漂流瓶的文字内容。")
    images: List[Image] = Field([], description="漂流瓶中的图片列表。")
    sender: str = Field(..., description="发送者昵称。")
    sender_id: str = Field(..., description="发送者唯一ID。")
    poke: bool = Field(..., description="是否戳一戳。")

class BottleOut(BaseModel):
    """漂流瓶的响应模型 (用于添加和捡起操作)。"""
    bottle_id: int = Field(..., description="漂流瓶的唯一ID（整数）。")
    content: str = Field(..., description="漂流瓶的文字内容。")
    images: List[Image] = Field([], description="漂流瓶中的图片列表。")
    sender: str = Field(..., description="发送者昵称。")
    sender_id: str = Field(..., description="发送者唯一ID。")
    picked: bool = Field(..., description="该漂流瓶是否已被捡起（全局状态）。")
    timestamp: str = Field(..., description="漂流瓶创建的时间戳 (YYYY-MM-DD HH:MM:SS)。")
    poke: bool = Field(..., description="是否戳一戳。")

    @classmethod
    def from_mongo_dict(cls, data: Dict[str, Any]) -> "BottleOut":
        # 如果 MongoDB 没有 'bottle_id' 字段，需要在这里处理默认值或报错
        return cls(**data)

class BottleCountOut(BaseModel):
    """漂流瓶数量统计的响应模型。"""
    total_active_bottles: int = Field(..., description="当前未被捡起的漂流瓶总数。")


@app.on_event("startup")
async def startup_db_client():
    global client, db, bottles_collection
    print("Attempting to connect to MongoDB...")
    try:
        client = MongoClient(MONGO_URI, server_api=ServerApi("1"))
        db = client[DATABASE_NAME]
        bottles_collection = db[COLLECTION_NAME]
        await client.admin.command('ping')
        print("Successfully connected to MongoDB!")
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to connect to database on startup: {e}"
        )

@app.on_event("shutdown")
async def shutdown_db_client():
    global client
    if client:
        client.close()
        print("Disconnected from MongoDB.")

# 新增原子计数器函数
async def get_next_sequence_value(sequence_name: str) -> int:
    """
    原子地获取并增加一个序列的值。
    """
    counters_collection = db["counters"]
    sequence_document = await counters_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'seq': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return sequence_document['seq']

@app.get("/", summary="根路径健康检查")
async def read_root():
    return {"message": "Welcome to the Drift Bottle Core API!"}

@app.post(
    "/bottles/",
    response_model=BottleOut,
    status_code=status.HTTP_201_CREATED,
    summary="添加新漂流瓶"
)
async def add_bottle(bottle_in: BottleIn):
    """
    创建一个新的漂流瓶并存储到MongoDB。
    """
    try:
        bottle_data = bottle_in.dict()
        
        # 使用新的原子ID生成方式
        new_id = await get_next_sequence_value("bottle_id")
        
        bottle_data.update({
            "bottle_id": new_id,
            "picked": False,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        
        # MongoDB 插入操作会自动添加一个 _id 字段
        await bottles_collection.insert_one(bottle_data)
        
        return BottleOut.from_mongo_dict(bottle_data)
    except Exception as e:
        print(f"Error adding bottle: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while adding bottle: {e}"
        )


@app.post(
    "/bottles/pick/{sender_id}",
    response_model=BottleOut,
    summary="随机捡起一个漂流瓶并返回其信息"
)
async def pick_random_bottle(sender_id: str):
    """
    随机选择一个未被捡起且非当前用户发送的漂流瓶，并将其全局状态标记为已捡起。
    返回被捡起瓶子的完整信息。
    客户端应将此信息存储在本地。
    """
    pipeline = [
        {"$match": {"picked": False, "sender_id": {"$ne": sender_id}}},
        {"$sample": {"size": 1}}
    ]
    
    try:
        bottles = await bottles_collection.aggregate(pipeline).to_list(length=1)
        
        if not bottles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No available bottles to pick."
            )
        bottle_doc = bottles[0] # 获取随机到的瓶子文档
        
        # 将瓶子在 MongoDB 中标记为已捡起
        # 这里的 _id 字段是从 aggregate 结果中获取的 MongoDB 的 ObjectId
        await bottles_collection.update_one(
            {"_id": bottle_doc["_id"]},
            {"$set": {"picked": True}}
        )
        
        # 返回被捡起瓶子的完整信息给客户端，客户端负责本地存储
        return BottleOut.from_mongo_dict(bottle_doc)
    except HTTPException: # 如果是上面抛出的404，直接重新抛出
        raise
    except Exception as e:
        print(f"Error picking random bottle for {sender_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while picking bottle: {e}"
        )

@app.get(
    "/bottles/counts/active",
    response_model=BottleCountOut,
    summary="获取当前未被捡起的漂流瓶总数"
)
async def get_active_bottle_counts():
    """
    获取当前系统中所有未被捡起的漂流瓶的总数量。
    """
    try:
        total_active_bottles = await bottles_collection.count_documents({"picked": False})
        
        return BottleCountOut(total_active_bottles=total_active_bottles)
    except Exception as e:
        print(f"Error getting active bottle counts: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while getting bottle counts: {e}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)