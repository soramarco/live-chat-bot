from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Stockage temporaire du mème en cours
current_meme = {
    "url": "",
    "caption": "",
    "author_name": "",
    "author_avatar": "",
    "active": False
}

@app.route("/")
def index():
    return render_template("overlay.html", meme=current_meme)

@app.route("/send_meme", methods=["POST"])
def send_meme():
    global current_meme
    data = request.json
    if data:
        current_meme = {
            "url": data.get("url", ""),
            "caption": data.get("caption", ""),
            "author_name": data.get("author_name", ""),
            "author_avatar": data.get("author_avatar", ""),
            "active": True
        }
    return jsonify({"status": "success"})

@app.route("/stop_meme", methods=["POST"])
def stop_meme():
    global current_meme
    current_meme["active"] = False
    current_meme["url"] = ""
    return jsonify({"status": "stopped"})

@app.route("/get_meme", methods=["GET"])
def get_meme():
    return jsonify(current_meme)

if __name__ == "__main__":
    app.run(port=5000, debug=True)