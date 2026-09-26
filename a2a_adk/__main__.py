"""Module entry point to run the agent server with Uvicorn.

Usage:
    python -m a2a_adk --host 0.0.0.0 --port 8000 --reload
    uvicorn a2a_adk.main:app --host 0.0.0.0 --port 8000
"""

from .cli import main

if __name__ == "__main__":
    main()
