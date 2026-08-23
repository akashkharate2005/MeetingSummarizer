import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["OPENAI_API_KEY"] = "test"

def test_project_smoke():
    from app.main import app
    assert app.title == "Meeting Summarizer"
