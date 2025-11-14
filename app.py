import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

import qrcode


BASE_DIR = Path(__file__).resolve().parent
QR_DIR = BASE_DIR / "static" / "qr"
QR_DIR.mkdir(parents=True, exist_ok=True)


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-key"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///ecobin.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        API_TOKEN=os.environ.get("ECOBIN_API_TOKEN", "changeme"),
    )
    return app


def create_db(app: Flask) -> SQLAlchemy:
    db = SQLAlchemy()
    db.init_app(app)
    return db


app = create_app()
db = create_db(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


LEVELS = [
    ("Новичок", 0),
    ("Собирающий", 100),
    ("Эко-герой", 200),
    ("Наставник", 400),
    ("Легенда", 800),
]


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    points = db.Column(db.Integer, default=0)
    language = db.Column(db.String(10), default="ru")
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    rewards = db.relationship("RewardToken", back_populates="redeemed_by", lazy="dynamic")
    market_items = db.relationship("MarketItem", back_populates="created_by", lazy="dynamic")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def current_level(self) -> dict:
        for index, (name, threshold) in enumerate(reversed(LEVELS)):
            if self.points >= threshold:
                level_index = len(LEVELS) - index - 1
                level_name, level_threshold = LEVELS[level_index]
                next_level_threshold = None
                if level_index + 1 < len(LEVELS):
                    next_level_threshold = LEVELS[level_index + 1][1]
                return {
                    "index": level_index + 1,
                    "name": level_name,
                    "threshold": level_threshold,
                    "next_threshold": next_level_threshold,
                }
        return {
            "index": 1,
            "name": LEVELS[0][0],
            "threshold": LEVELS[0][1],
            "next_threshold": LEVELS[1][1] if len(LEVELS) > 1 else None,
        }

    @property
    def progress_to_next_level(self) -> float:
        info = self.current_level
        next_threshold = info["next_threshold"]
        if next_threshold is None:
            return 1.0
        span = next_threshold - info["threshold"]
        progress = self.points - info["threshold"]
        return min(max(progress / span, 0.0), 1.0)


class RewardToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False)
    material = db.Column(db.String(32), nullable=False)
    points = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    redeemed = db.Column(db.Boolean, default=False)
    redeemed_at = db.Column(db.DateTime, nullable=True)

    redeemed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    redeemed_by = db.relationship("User", back_populates="rewards")


class MarketItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", back_populates="market_items")


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


@app.context_processor
def inject_levels():
    return {"LEVELS": LEVELS, "datetime": datetime}


def ensure_admin_user() -> None:
    admin_username = os.environ.get("ADMIN_USERNAME", "admin")
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    admin = User.query.filter(func.lower(User.username) == admin_username.lower()).first()
    if not admin:
        admin = User(
            username=admin_username,
            email=admin_email,
            is_admin=True,
            language="ru",
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()


def greeting_for_now(name: str) -> str:
    now = datetime.now().hour
    if 5 <= now < 12:
        template = "Доброе утро, {name}!"
    elif 12 <= now < 18:
        template = "Добрый день, {name}!"
    elif 18 <= now < 23:
        template = "Добрый вечер, {name}!"
    else:
        template = "Доброй ночи, {name}!"
    return template.format(name=name)


def qr_image_path(token_value: str) -> Path:
    return QR_DIR / f"{token_value}.png"


def build_qr_code(token_value: str, redeem_url: str) -> Path:
    img = qrcode.make(redeem_url)
    path = qr_image_path(token_value)
    img.save(path)
    return path


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip() or None
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not password:
            flash("Логин и пароль обязательны.", "danger")
        elif password != confirm:
            flash("Пароли не совпадают.", "danger")
        elif User.query.filter(func.lower(User.username) == username.lower()).first():
            flash("Пользователь с таким именем уже существует.", "danger")
        else:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Добро пожаловать в EcoBin!", "success")
            return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Вы успешно вошли.", "success")
            next_page = request.args.get("next") or url_for("dashboard")
            return redirect(next_page)
        flash("Неверный логин или пароль.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Вы вышли из системы.", "info")
    return redirect(url_for("landing"))


@app.route("/dashboard")
@login_required
def dashboard():
    greeting = greeting_for_now(current_user.username)
    level_info = current_user.current_level
    return render_template(
        "dashboard.html",
        greeting=greeting,
        level_info=level_info,
        progress=current_user.progress_to_next_level,
        RewardToken=RewardToken,
    )


@app.route("/scan", methods=["GET", "POST"])
@login_required
def scan():
    if request.method == "POST":
        token_value = request.form.get("token", "").strip()
        if not token_value:
            flash("Введите код для начисления баллов.", "danger")
        else:
            reward = RewardToken.query.filter_by(token=token_value).first()
            if reward is None:
                flash("Код не найден.", "danger")
            elif reward.redeemed:
                flash("Этот код уже использован.", "warning")
            else:
                reward.redeemed = True
                reward.redeemed_at = datetime.utcnow()
                reward.redeemed_by = current_user
                current_user.points += reward.points
                db.session.commit()
                flash(f"Начислено {reward.points} баллов за {reward.material}.", "success")
                return redirect(url_for("dashboard"))

    available_rewards = (
        RewardToken.query.filter_by(redeemed=False)
        .order_by(RewardToken.created_at.desc())
        .limit(5)
        .all()
    )
    return render_template("scan.html", available_rewards=available_rewards)


@app.route("/market")
@login_required
def market():
    items = MarketItem.query.order_by(MarketItem.created_at.desc()).all()
    return render_template("market.html", items=items)


@app.route("/market/manage", methods=["GET", "POST"])
@login_required
def manage_market():
    if not current_user.is_admin:
        abort(403)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price", "0").strip()
        try:
            price_value = int(price)
        except ValueError:
            price_value = 0

        if not name:
            flash("Название товара обязательно.", "danger")
        else:
            item = MarketItem(name=name, description=description, price=price_value, created_by=current_user)
            db.session.add(item)
            db.session.commit()
            flash("Товар добавлен.", "success")
            return redirect(url_for("market"))

    items = MarketItem.query.order_by(MarketItem.created_at.desc()).all()
    return render_template("manage_market.html", items=items)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_info":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip() or None
            language = request.form.get("language", current_user.language)

            if username and username != current_user.username:
                existing = User.query.filter(func.lower(User.username) == username.lower()).first()
                if existing and existing.id != current_user.id:
                    flash("Это имя уже занято.", "danger")
                    return redirect(url_for("profile"))
                current_user.username = username
            current_user.email = email
            current_user.language = language
            db.session.commit()
            flash("Профиль обновлён.", "success")
            return redirect(url_for("profile"))
        elif action == "change_password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not current_user.check_password(current_password):
                flash("Текущий пароль неверный.", "danger")
            elif new_password != confirm_password or not new_password:
                flash("Новый пароль некорректен.", "danger")
            else:
                current_user.set_password(new_password)
                db.session.commit()
                flash("Пароль изменён.", "success")
                return redirect(url_for("profile"))

    return render_template("profile.html")


@app.route("/reward/<token>")
def reward_details(token: str):
    reward = RewardToken.query.filter_by(token=token).first_or_404()
    return render_template("reward.html", reward=reward)


@app.route("/reward/<token>/redeem", methods=["POST"])
@login_required
def redeem_reward(token: str):
    reward = RewardToken.query.filter_by(token=token).first_or_404()
    if reward.redeemed:
        flash("Этот код уже был использован.", "warning")
        return redirect(url_for("dashboard"))

    reward.redeemed = True
    reward.redeemed_at = datetime.utcnow()
    reward.redeemed_by = current_user
    current_user.points += reward.points
    db.session.commit()
    flash(f"Вы получили {reward.points} баллов!", "success")
    return redirect(url_for("dashboard"))


@app.route("/reward/<token>/qrcode")
def serve_qr(token: str):
    reward = RewardToken.query.filter_by(token=token).first_or_404()
    redeem_url = url_for("reward_details", token=token, _external=True)
    path = qr_image_path(token)
    if not path.exists():
        build_qr_code(token, redeem_url)
    return send_file(path, mimetype="image/png")


@app.post("/api/reward")
def api_reward():
    api_key = request.headers.get("X-API-KEY")
    if api_key != app.config["API_TOKEN"]:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    material = (payload.get("material") or "").lower()
    if material not in {"bottle", "paper"}:
        return jsonify({"error": "material must be 'bottle' or 'paper'"}), 400

    points = 100 if material == "bottle" else 50
    token_value = uuid4().hex
    reward = RewardToken(token=token_value, material=material, points=points)
    db.session.add(reward)
    db.session.commit()

    redeem_url = url_for("reward_details", token=token_value, _external=True)
    build_qr_code(token_value, redeem_url)
    qr_url = url_for("serve_qr", token=token_value, _external=True)

    return jsonify(
        {
            "token": token_value,
            "material": material,
            "points": points,
            "redeem_url": redeem_url,
            "qr_url": qr_url,
        }
    )


@app.errorhandler(403)
def forbidden(_error):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("errors/404.html"), 404


def setup_database():
    with app.app_context():
        db.create_all()
        ensure_admin_user()


setup_database()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
