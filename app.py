from __future__ import annotations

import csv
import json
import os
import re
import shutil
from functools import wraps
from io import BytesIO
from uuid import uuid4

from flask import Flask, redirect, render_template, request, send_file, session, url_for
from PIL import Image, ImageDraw, ImageFont
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_DIR = os.path.join(BASE_DIR, "events")
GENERATED_DIR = os.path.join(BASE_DIR, "generated_certificates")
FONT_PATH = os.path.join(BASE_DIR, "fonts", "Montserrat-Bold.ttf")

os.makedirs(EVENTS_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# ─── First-run migration ──────────────────────────────────────────────────────

def _migrate_legacy_event() -> None:
	"""Copy certificate_template.png + data.csv into events/think-run-debug/ on first run."""
	slug = "think-run-debug"
	edir = os.path.join(EVENTS_DIR, slug)
	config_path = os.path.join(edir, "config.json")
	if os.path.exists(config_path):
		return
	os.makedirs(edir, exist_ok=True)
	legacy_template = os.path.join(BASE_DIR, "certificate_template.png")
	if os.path.exists(legacy_template):
		shutil.copy2(legacy_template, os.path.join(edir, "template.png"))
	legacy_csv = os.path.join(BASE_DIR, "data.csv")
	if os.path.exists(legacy_csv):
		shutil.copy2(legacy_csv, os.path.join(edir, "data.csv"))
	config = {
		"name": "Think, Run, Debug",
		"slug": slug,
		"active": True,
		"validation_type": "player_team",
		"text_x": 1789,
		"text_y": 1440,
		"font_size": 100,
		"font_color": [50, 34, 24],
	}
	with open(config_path, "w", encoding="utf-8") as f:
		json.dump(config, f, indent=2)


_migrate_legacy_event()


# ─── Event helpers ────────────────────────────────────────────────────────────

def safe_slug(slug: str) -> bool:
	return bool(_SLUG_RE.match(slug)) and ".." not in slug and len(slug) <= 80


def _event_dir(slug: str) -> str:
	return os.path.join(EVENTS_DIR, slug)


def _event_config_path(slug: str) -> str:
	return os.path.join(_event_dir(slug), "config.json")


def _event_template_path(slug: str) -> str:
	return os.path.join(_event_dir(slug), "template.png")


def _event_csv_path(slug: str) -> str:
	return os.path.join(_event_dir(slug), "data.csv")


def load_event(slug: str) -> dict | None:
	if not safe_slug(slug):
		return None
	path = _event_config_path(slug)
	if not os.path.exists(path):
		return None
	with open(path, encoding="utf-8") as f:
		return json.load(f)


def save_event_config(slug: str, config: dict) -> None:
	with open(_event_config_path(slug), "w", encoding="utf-8") as f:
		json.dump(config, f, indent=2)


def load_all_events(active_only: bool = False) -> list[dict]:
	events = []
	if not os.path.isdir(EVENTS_DIR):
		return events
	for slug in os.listdir(EVENTS_DIR):
		config = load_event(slug)
		if config is None:
			continue
		if active_only and not config.get("active", False):
			continue
		events.append(config)
	events.sort(key=lambda e: e.get("name", "").lower())
	return events


def normalize_value(value: str) -> str:
	return (value or "").strip().lower()


def load_valid_participants(slug: str) -> set[tuple[str, str]]:
	participants: set[tuple[str, str]] = set()
	path = _event_csv_path(slug)
	if not os.path.exists(path):
		return participants
	with open(path, newline="", encoding="utf-8") as f:
		reader = csv.DictReader(f)
		for row in reader:
			player = normalize_value(row.get("player", ""))
			team = normalize_value(row.get("team", ""))
			if player and team:
				participants.add((player, team))
	return participants


def load_valid_names(slug: str) -> set[str]:
	names: set[str] = set()
	path = _event_csv_path(slug)
	if not os.path.exists(path):
		return names
	with open(path, newline="", encoding="utf-8") as f:
		reader = csv.DictReader(f)
		for row in reader:
			name = normalize_value(row.get("name", ""))
			if name:
				names.add(name)
	return names


def load_team_names(slug: str) -> list[str]:
	path = _event_csv_path(slug)
	if not os.path.exists(path):
		return []
	seen: set[str] = set()
	teams: list[str] = []
	with open(path, newline="", encoding="utf-8") as f:
		reader = csv.DictReader(f)
		for row in reader:
			team_raw = (row.get("team", "") or "").strip()
			key = normalize_value(team_raw)
			if team_raw and key not in seen:
				seen.add(key)
				teams.append(team_raw)
	return sorted(teams, key=lambda v: v.lower())


# ─── Certificate helpers ──────────────────────────────────────────────────────

def get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
	try:
		if os.path.exists(FONT_PATH):
			return ImageFont.truetype(FONT_PATH, size=size)
		return ImageFont.truetype("arial.ttf", size=size)
	except Exception:
		return ImageFont.load_default()


def _cert_metadata_path(cert_id: str) -> str:
	return os.path.join(GENERATED_DIR, f"{cert_id}.json")


def _cert_image_path(cert_id: str) -> str:
	return os.path.join(GENERATED_DIR, f"{cert_id}.png")


def generate_certificate_file(slug: str, cert_name: str, event_config: dict) -> str:
	image = Image.open(_event_template_path(slug)).convert("RGBA")
	cert_id = uuid4().hex
	image.save(_cert_image_path(cert_id), format="PNG")
	metadata = {
		"event_slug": slug,
		"cert_name": cert_name,
		"text_x": event_config.get("text_x", 1789),
		"text_y": event_config.get("text_y", 1440),
		"font_size": event_config.get("font_size", 100),
		"font_color": event_config.get("font_color", [50, 34, 24]),
	}
	with open(_cert_metadata_path(cert_id), "w", encoding="utf-8") as f:
		json.dump(metadata, f)
	return cert_id


def load_cert_metadata(cert_id: str) -> dict | None:
	path = _cert_metadata_path(cert_id)
	if not os.path.exists(path):
		return None
	with open(path, encoding="utf-8") as f:
		return json.load(f)


def draw_name_on_image(image: Image.Image, metadata: dict) -> None:
	draw = ImageDraw.Draw(image)
	font = get_font(metadata.get("font_size", 100))
	color = tuple(metadata.get("font_color", [50, 34, 24]))
	draw.text(
		(metadata.get("text_x", 1789), metadata.get("text_y", 1440)),
		metadata.get("cert_name", ""),
		fill=color,
		font=font,
		anchor="mm",
	)


def safe_download_name(name: str, slug: str) -> str:
	cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", (name or "").strip())
	cleaned = re.sub(r"\s+", "-", cleaned)
	cleaned = cleaned.strip("-")
	if not cleaned:
		cleaned = f"{slug}-certificate"
	return f"{cleaned}.png"


def _is_valid_png(stream) -> bool:
	header = stream.read(8)
	stream.seek(0)
	return header[:8] == b"\x89PNG\r\n\x1a\n"


def _parse_int(value: str | None, fallback: int) -> int:
	try:
		return max(0, int(value)) if value is not None else fallback
	except (TypeError, ValueError):
		return fallback


def _parse_color(value: str | None, fallback: list | None = None) -> list[int]:
	if fallback is None:
		fallback = [50, 34, 24]
	if not value:
		return fallback
	value = value.strip().lstrip("#")
	if len(value) == 6:
		try:
			return [int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)]
		except ValueError:
			pass
	return fallback


# ─── Admin auth ───────────────────────────────────────────────────────────────

def require_admin(f):
	@wraps(f)
	def decorated(*args, **kwargs):
		if not session.get("admin_logged_in"):
			return redirect(url_for("admin_login"))
		return f(*args, **kwargs)
	return decorated


# ─── Public routes ────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def home():
	return render_template("index.html", events=load_all_events(active_only=True))


@app.route("/events/<slug>", methods=["GET"])
def event_page(slug: str):
	if not safe_slug(slug):
		return redirect(url_for("home"))
	config = load_event(slug)
	if config is None or not config.get("active", False):
		return redirect(url_for("home"))
	validation_type = config.get("validation_type", "player_team")
	teams = load_team_names(slug) if validation_type == "player_team" else []
	return render_template("event.html", event=config, teams=teams, error=None)


@app.route("/events/<slug>/download", methods=["POST"])
def download_certificate(slug: str):
	if not safe_slug(slug):
		return redirect(url_for("home"))
	config = load_event(slug)
	if config is None or not config.get("active", False):
		return redirect(url_for("home"))
	validation_type = config.get("validation_type", "player_team")
	cert_name = (request.form.get("cert_name", "") or "").strip()
	if not cert_name:
		teams = load_team_names(slug) if validation_type == "player_team" else []
		return render_template("event.html", event=config, teams=teams, error="Please fill all fields."), 400
	if validation_type == "player_team":
		registration_name = normalize_value(request.form.get("registration_name", ""))
		team_name = normalize_value(request.form.get("team_name", ""))
		if not registration_name or not team_name:
			return render_template("event.html", event=config, teams=load_team_names(slug), error="Please fill all fields."), 400
		if (registration_name, team_name) not in load_valid_participants(slug):
			return render_template("event.html", event=config, teams=load_team_names(slug), error="Invalid player or team name."), 400
	else:
		registration_name = normalize_value(request.form.get("registration_name", ""))
		if not registration_name:
			return render_template("event.html", event=config, teams=[], error="Please fill all fields."), 400
		if registration_name not in load_valid_names(slug):
			return render_template("event.html", event=config, teams=[], error="Name not found in participant list."), 400
	if not os.path.exists(_event_template_path(slug)):
		teams = load_team_names(slug) if validation_type == "player_team" else []
		return render_template("event.html", event=config, teams=teams, error="Certificate template not found on server."), 500
	try:
		cert_id = generate_certificate_file(slug, cert_name, config)
	except Exception:
		teams = load_team_names(slug) if validation_type == "player_team" else []
		return render_template("event.html", event=config, teams=teams, error="Something went wrong generating your certificate."), 500
	return redirect(url_for("preview_page", cert_id=cert_id))


@app.route("/preview/<cert_id>", methods=["GET"])
def preview_page(cert_id: str):
	if not re.match(r"^[a-f0-9]{32}$", cert_id):
		return redirect(url_for("home"))
	metadata = load_cert_metadata(cert_id)
	if metadata is None or not os.path.exists(_cert_image_path(cert_id)):
		return redirect(url_for("home"))
	event = load_event(metadata["event_slug"])
	return render_template("preview.html", cert_id=cert_id, cert_name=metadata["cert_name"], event=event)


@app.route("/preview-image/<cert_id>", methods=["GET"])
def preview_image(cert_id: str):
	if not re.match(r"^[a-f0-9]{32}$", cert_id):
		return ("Not found", 404)
	metadata = load_cert_metadata(cert_id)
	if metadata is None or not os.path.exists(_cert_image_path(cert_id)):
		return ("Not found", 404)
	image = Image.open(_cert_image_path(cert_id)).convert("RGBA")
	draw_name_on_image(image, metadata)
	output = BytesIO()
	image.save(output, format="PNG")
	output.seek(0)
	return send_file(output, mimetype="image/png")


@app.route("/download-file/<cert_id>", methods=["GET"])
def download_file(cert_id: str):
	if not re.match(r"^[a-f0-9]{32}$", cert_id):
		return ("Not found", 404)
	metadata = load_cert_metadata(cert_id)
	if metadata is None or not os.path.exists(_cert_image_path(cert_id)):
		return ("Not found", 404)
	image = Image.open(_cert_image_path(cert_id)).convert("RGBA")
	draw_name_on_image(image, metadata)
	output = BytesIO()
	image.save(output, format="PNG")
	output.seek(0)
	return send_file(
		output,
		mimetype="image/png",
		as_attachment=True,
		download_name=safe_download_name(metadata["cert_name"], metadata.get("event_slug", "event")),
	)


# ─── Admin routes ─────────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
	error = None
	if request.method == "POST":
		password = request.form.get("password", "")
		if not ADMIN_PASSWORD:
			error = "ADMIN_PASSWORD environment variable is not set on this server."
		elif password == ADMIN_PASSWORD:
			session["admin_logged_in"] = True
			return redirect(url_for("admin_dashboard"))
		else:
			error = "Incorrect password."
	return render_template("admin/login.html", error=error)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
	session.pop("admin_logged_in", None)
	return redirect(url_for("admin_login"))


@app.route("/admin", methods=["GET"])
@require_admin
def admin_dashboard():
	return render_template("admin/dashboard.html", events=load_all_events())


@app.route("/admin/events/new", methods=["GET"])
@require_admin
def admin_new_event():
	return render_template("admin/event_form.html", event=None, is_new=True, error=None)


@app.route("/admin/events/new", methods=["POST"])
@require_admin
def admin_create_event():
	name = (request.form.get("name", "") or "").strip()
	slug = (request.form.get("slug", "") or "").strip().lower()
	validation_type = request.form.get("validation_type", "player_team")
	text_x = _parse_int(request.form.get("text_x"), 1789)
	text_y = _parse_int(request.form.get("text_y"), 1440)
	font_size = _parse_int(request.form.get("font_size"), 100)
	font_color = _parse_color(request.form.get("font_color", ""))
	form_data = {"name": name, "slug": slug, "validation_type": validation_type,
				 "text_x": text_x, "text_y": text_y, "font_size": font_size, "font_color": font_color}
	if not name or not slug:
		return render_template("admin/event_form.html", event=form_data, is_new=True, error="Name and slug are required."), 400
	if not safe_slug(slug):
		return render_template("admin/event_form.html", event=form_data, is_new=True,
							   error="Slug must be lowercase letters, numbers, and hyphens only."), 400
	if os.path.exists(_event_config_path(slug)):
		return render_template("admin/event_form.html", event=form_data, is_new=True,
							   error=f"An event with slug '{slug}' already exists."), 400
	os.makedirs(_event_dir(slug), exist_ok=True)
	config = {"name": name, "slug": slug, "active": False, "validation_type": validation_type,
			  "text_x": text_x, "text_y": text_y, "font_size": font_size, "font_color": font_color}
	save_event_config(slug, config)
	return redirect(url_for("admin_edit_event", slug=slug))


@app.route("/admin/events/<slug>", methods=["GET"])
@require_admin
def admin_edit_event(slug: str):
	if not safe_slug(slug):
		return redirect(url_for("admin_dashboard"))
	config = load_event(slug)
	if config is None:
		return redirect(url_for("admin_dashboard"))
	return render_template("admin/event_form.html", event=config, is_new=False, error=None,
						   has_template=os.path.exists(_event_template_path(slug)),
						   has_csv=os.path.exists(_event_csv_path(slug)))


@app.route("/admin/events/<slug>/config", methods=["POST"])
@require_admin
def admin_update_config(slug: str):
	if not safe_slug(slug):
		return redirect(url_for("admin_dashboard"))
	config = load_event(slug)
	if config is None:
		return redirect(url_for("admin_dashboard"))
	config["name"] = (request.form.get("name", "") or config["name"]).strip()
	config["validation_type"] = request.form.get("validation_type", config.get("validation_type", "player_team"))
	config["text_x"] = _parse_int(request.form.get("text_x"), config.get("text_x", 1789))
	config["text_y"] = _parse_int(request.form.get("text_y"), config.get("text_y", 1440))
	config["font_size"] = _parse_int(request.form.get("font_size"), config.get("font_size", 100))
	config["font_color"] = _parse_color(request.form.get("font_color"), config.get("font_color", [50, 34, 24]))
	save_event_config(slug, config)
	return render_template("admin/event_form.html", event=config, is_new=False, success="Settings saved.",
						   error=None, has_template=os.path.exists(_event_template_path(slug)),
						   has_csv=os.path.exists(_event_csv_path(slug)))


@app.route("/admin/events/<slug>/upload-template", methods=["POST"])
@require_admin
def admin_upload_template(slug: str):
	if not safe_slug(slug):
		return redirect(url_for("admin_dashboard"))
	config = load_event(slug)
	if config is None:
		return redirect(url_for("admin_dashboard"))
	has_csv = os.path.exists(_event_csv_path(slug))
	has_template = os.path.exists(_event_template_path(slug))
	file = request.files.get("template_file")
	if not file or file.filename == "":
		return render_template("admin/event_form.html", event=config, is_new=False,
							   error="No file selected.", has_template=has_template, has_csv=has_csv), 400
	if not secure_filename(file.filename).lower().endswith(".png"):
		return render_template("admin/event_form.html", event=config, is_new=False,
							   error="Template must be a PNG file.", has_template=has_template, has_csv=has_csv), 400
	if not _is_valid_png(file.stream):
		return render_template("admin/event_form.html", event=config, is_new=False,
							   error="File does not appear to be a valid PNG.", has_template=has_template, has_csv=has_csv), 400
	file.stream.seek(0)
	file.save(_event_template_path(slug))
	return render_template("admin/event_form.html", event=config, is_new=False,
						   success="Template uploaded successfully.", error=None, has_template=True, has_csv=has_csv)


@app.route("/admin/events/<slug>/upload-csv", methods=["POST"])
@require_admin
def admin_upload_csv(slug: str):
	if not safe_slug(slug):
		return redirect(url_for("admin_dashboard"))
	config = load_event(slug)
	if config is None:
		return redirect(url_for("admin_dashboard"))
	has_template = os.path.exists(_event_template_path(slug))
	has_csv = os.path.exists(_event_csv_path(slug))
	file = request.files.get("csv_file")
	if not file or file.filename == "":
		return render_template("admin/event_form.html", event=config, is_new=False,
							   error="No file selected.", has_template=has_template, has_csv=has_csv), 400
	if not secure_filename(file.filename).lower().endswith(".csv"):
		return render_template("admin/event_form.html", event=config, is_new=False,
							   error="Participants file must be a .csv.", has_template=has_template, has_csv=has_csv), 400
	content = file.stream.read().decode("utf-8", errors="replace")
	validation_type = config.get("validation_type", "player_team")
	required_headers = {"player", "team"} if validation_type == "player_team" else {"name"}
	try:
		reader = csv.DictReader(content.splitlines())
		headers = {h.strip().lower() for h in (reader.fieldnames or [])}
		if not required_headers.issubset(headers):
			missing = ", ".join(sorted(required_headers - headers))
			return render_template("admin/event_form.html", event=config, is_new=False,
								   error=f"CSV is missing required column(s): {missing}.",
								   has_template=has_template, has_csv=has_csv), 400
	except Exception:
		return render_template("admin/event_form.html", event=config, is_new=False,
							   error="Could not parse CSV file.", has_template=has_template, has_csv=has_csv), 400
	with open(_event_csv_path(slug), "w", encoding="utf-8", newline="") as f:
		f.write(content)
	return render_template("admin/event_form.html", event=config, is_new=False,
						   success="Participants CSV uploaded.", error=None, has_template=has_template, has_csv=True)


@app.route("/admin/events/<slug>/toggle", methods=["POST"])
@require_admin
def admin_toggle_event(slug: str):
	if not safe_slug(slug):
		return redirect(url_for("admin_dashboard"))
	config = load_event(slug)
	if config is None:
		return redirect(url_for("admin_dashboard"))
	config["active"] = not config.get("active", False)
	save_event_config(slug, config)
	return redirect(url_for("admin_dashboard"))


@app.route("/admin/events/<slug>/delete", methods=["POST"])
@require_admin
def admin_delete_event(slug: str):
	if not safe_slug(slug):
		return redirect(url_for("admin_dashboard"))
	if request.form.get("confirm", "") != slug:
		return redirect(url_for("admin_dashboard"))
	event_path = _event_dir(slug)
	if os.path.isdir(event_path):
		shutil.rmtree(event_path)
	return redirect(url_for("admin_dashboard"))


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
	app.run(debug=True)
