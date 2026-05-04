from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route("/create-user", methods=["POST"])
def create_user():
    """Verb in path — should be POST /users."""
    data = request.json
    return jsonify({"id": 1, "name": data["name"]}), 201


@app.route("/order/<int:order_id>", methods=["GET"])
def get_order(order_id):
    """Singular noun — should be /orders/<id>."""
    return jsonify({"id": order_id, "status": "shipped"})


@app.route("/users/<int:user_id>/orders/<int:order_id>/items/<int:item_id>/reviews", methods=["GET"])
def get_review(user_id, order_id, item_id):
    """Deeply nested — should flatten beyond two levels."""
    return jsonify({"reviews": []})


@app.route("/v1/products", methods=["GET"])
@app.route("/categories", methods=["GET"])
def mixed_versioning():
    """Mixes versioned and unversioned paths."""
    return jsonify([])
