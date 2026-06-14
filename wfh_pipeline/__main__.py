"""Allows `python -m wfh_pipeline ...` as the CLI entrypoint."""
from .cli import app

if __name__ == "__main__":
    app()
