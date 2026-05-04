import traceback
from flask import Flask, jsonify

app = Flask(__name__)


@app.errorhandler(400)
def bad_request(error):
    return jsonify({"error": {"code": "BAD_REQUEST", "message": str(error)}}), 400


@app.errorhandler(404)
def not_found(error):
    # Inconsistent format — returns flat string instead of error envelope
    return jsonify({"message": "Not found"}), 404


@app.errorhandler(422)
def unprocessable(error):
    # Yet another format — array of strings
    return jsonify({"errors": ["Validation failed"]}), 422


@app.errorhandler(500)
def server_error(error):
    # Leaks stack trace to the client
    return jsonify({
        "error": "Internal server error",
        "traceback": traceback.format_exc(),
    }), 500
