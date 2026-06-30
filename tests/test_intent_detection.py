import asyncio
import os
import sys

# Thêm root path vào PYTHONPATH để import được src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.ai.task_parser import get_parser

async def test_intents():
    print("🚀 Khởi chạy test intent detection...")
    
    parser = get_parser()
    
    test_cases = [
        "Em xin phép nghỉ ốm ngày hôm nay nhé sếp @ducanh.",
        "Em đi muộn 30p do tắc đường nhé sếp ơi.",
        "Nhờ anh lùi deadline task T-001 thêm 2 ngày vì có lỗi phát sinh.",
        "Làm tính năng checkout, cần tích hợp payment, deadline tuần sau",
        "Chào buổi sáng cả team!"
    ]
    
    for text in test_cases:
        print(f"\n💬 Test message: '{text}'")
        try:
            res = await parser.detect_intent(text)
            print(f"👉 Intent: {res.intent}")
            print(f"👉 Details: leave_type={res.leave_type}, start_date={res.start_date}, end_date={res.end_date}, reason={res.reason}, task_key={res.task_key}, days={res.days_to_extend}")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    # Đọc file env nếu có
    from dotenv import load_dotenv
    load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env')))
    
    asyncio.run(test_intents())
