import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
SAMPLE_DATA_DIR = os.path.join(BASE_DIR, 'sample_data')
ATTACHMENTS_DIR = os.path.join(DATA_DIR, 'attachments')


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'ticket-tracker-dev-key')
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(DATA_DIR, 'tickets.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max upload
    ZENDESK_BASE_URL = 'https://redislabs.zendesk.com/agent/tickets'


def ensure_dirs():
    """Create required data directories if they don't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
