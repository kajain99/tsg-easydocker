import os

from flask import Flask

from auth_utils import configure_app, register_security
from routes import register_routes


app = Flask(__name__)
configure_app(app)
register_security(app)
register_routes(app)


if __name__ == "__main__":
    app.run(
        host=os.environ.get("EASYDOCKER_HOST", "127.0.0.1"),
        port=int(os.environ.get("EASYDOCKER_PORT", "5000"))
    )
