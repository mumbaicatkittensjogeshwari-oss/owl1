import uvicorn

if __name__ == "__main__":
    print("🦉 OWL Terminal Backend Starting...")
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )
