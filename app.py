from __future__ import annotations

import csv
import os
import re
from io import BytesIO
from uuid import uuid4

from flask import Flask, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageDraw, ImageFont


app = Flask(__name__)

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "data.csv")
TEMPLATE_PATH = os.path.join(BASE_DIR, "certificate_template.png")
FONT_PATH = os.path.join(BASE_DIR, "fonts", "montserrat-bold.ttf")
GENERATED_DIR = os.path.join(BASE_DIR, "generated_certificates")
os.makedirs(GENERATED_DIR, exist_ok=True)

# Text rendering configuration (easy to tune)
NAME_FONT_SIZE = 100
NAME_COLOR = (50, 34, 24)  # dark brown
NAME_Y = 1440  # confirmed final name center Y coordinate
NAME_X = 1789  # center of dotted line (starts at 395px, ends at image_width - 325)
DEBUG_LINE_DEFAULT_Y = 1484


def normalize_value(value: str) -> str:
	"""Trim and lowercase input safely."""
	return (value or "").strip().lower()


def load_valid_participants() -> set[tuple[str, str]]:
	"""Load player/team pairs from CSV as normalized tuples."""
	participants: set[tuple[str, str]] = set()

	if not os.path.exists(CSV_PATH):
		return participants

	with open(CSV_PATH, newline="", encoding="utf-8") as csvfile:
		reader = csv.DictReader(csvfile)
		for row in reader:
			player = normalize_value(row.get("player", ""))
			team = normalize_value(row.get("team", ""))
			if player and team:
				participants.add((player, team))

	return participants


def load_team_names() -> list[str]:
	"""Load unique team names from CSV for UI suggestions."""
	if not os.path.exists(CSV_PATH):
		return []

	seen_normalized: set[str] = set()
	teams: list[str] = []

	with open(CSV_PATH, newline="", encoding="utf-8") as csvfile:
		reader = csv.DictReader(csvfile)
		for row in reader:
			team_raw = (row.get("team", "") or "").strip()
			team_key = normalize_value(team_raw)
			if team_raw and team_key and team_key not in seen_normalized:
				seen_normalized.add(team_key)
				teams.append(team_raw)

	return sorted(teams, key=lambda value: value.lower())


def get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
	"""Load preferred TrueType font with a safe fallback."""
	try:
		if os.path.exists(FONT_PATH):
			return ImageFont.truetype(FONT_PATH, size=size)
		return ImageFont.truetype("arial.ttf", size=size)
	except Exception:
		return ImageFont.load_default()


def generate_certificate_file(cert_name: str) -> str:
	"""Generate certificate image file and return certificate id."""
	image = Image.open(TEMPLATE_PATH).convert("RGBA")

	cert_id = uuid4().hex
	output_path = os.path.join(GENERATED_DIR, f"{cert_id}.png")
	image.save(output_path, format="PNG")
	return cert_id


def certificate_path_from_id(cert_id: str) -> str:
	"""Get generated certificate file path from its id."""
	return os.path.join(GENERATED_DIR, f"{cert_id}.png")


def parse_line_y(value: str | None) -> int:
	"""Parse line y position from query/form with safe fallback."""
	try:
		if value is None:
			return DEBUG_LINE_DEFAULT_Y
		line_y = int(value)
		return max(0, line_y)
	except (TypeError, ValueError):
		return DEBUG_LINE_DEFAULT_Y


def parse_axis_value(value: str | None, fallback: int) -> int:
	"""Parse generic axis value from query/form with fallback."""
	try:
		if value is None:
			return fallback
		parsed_value = int(value)
		return max(0, parsed_value)
	except (TypeError, ValueError):
		return fallback


def get_image_size(path: str) -> tuple[int, int]:
	"""Read image dimensions safely."""
	with Image.open(path) as image:
		return image.size


def safe_download_name(name: str) -> str:
	"""Create a filesystem-safe filename from a certificate name."""
	cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", (name or "").strip())
	cleaned = re.sub(r"\s+", "-", cleaned)
	cleaned = cleaned.strip("-")
	if not cleaned:
		cleaned = "think-run-debug-certificate"
	return f"{cleaned}.png"


@app.route("/", methods=["GET"])
def home():
	return render_template("index.html", error=None, teams=load_team_names())


@app.route("/download", methods=["GET", "POST"])
def download_certificate():
	if request.method == "GET":
		return redirect(url_for("home"))

	registration_name = normalize_value(request.form.get("registration_name", ""))
	team_name = normalize_value(request.form.get("team_name", ""))
	cert_name = (request.form.get("cert_name", "") or "").strip()

	if not registration_name or not team_name or not cert_name:
		return (
			render_template("index.html", error="Please fill all fields.", teams=load_team_names()),
			400,
		)

	valid_participants = load_valid_participants()
	if (registration_name, team_name) not in valid_participants:
		return (
			render_template(
				"index.html",
				error="Invalid player or team name.",
				teams=load_team_names(),
			),
			400,
		)

	try:
		if not os.path.exists(TEMPLATE_PATH):
			return (
				render_template(
					"index.html",
					error="Certificate template not found on server.",
					teams=load_team_names(),
				),
				500,
			)

		cert_id = generate_certificate_file(cert_name)
		certificate_path = certificate_path_from_id(cert_id)
		image_width, image_height = get_image_size(certificate_path)
		return render_template(
			"preview.html",
			cert_id=cert_id,
			cert_name=cert_name,
			image_width=image_width,
			image_height=image_height,
		)
	except Exception:
		return (
			render_template(
				"index.html",
				error="Something went wrong while generating your certificate.",
				teams=load_team_names(),
			),
			500,
		)


@app.route("/preview/<cert_id>", methods=["GET"])
def preview_page(cert_id: str):
	certificate_path = certificate_path_from_id(cert_id)
	if not os.path.exists(certificate_path):
		return render_template("index.html", error="Certificate preview not found.", teams=load_team_names()), 404

	cert_name = (request.args.get("cert_name", "") or "").strip()
	line_y = parse_line_y(request.args.get("line_y"))
	image_width, image_height = get_image_size(certificate_path)
	line_y = min(line_y, image_height - 1)

	return render_template(
		"preview.html",
		cert_id=cert_id,
		cert_name=cert_name,
		image_width=image_width,
		image_height=image_height,
	)


@app.route("/preview-image/<cert_id>", methods=["GET"])
def preview_image(cert_id: str):
	certificate_path = certificate_path_from_id(cert_id)
	if not os.path.exists(certificate_path):
		return render_template("index.html", error="Certificate preview not found.", teams=load_team_names()), 404

	cert_name = (request.args.get("cert_name", "") or "").strip()

	image = Image.open(certificate_path).convert("RGBA")
	draw = ImageDraw.Draw(image)
	width, height = image.size

	font = get_font(NAME_FONT_SIZE)
	draw.text((NAME_X, NAME_Y), cert_name, fill=NAME_COLOR, font=font, anchor="mm")

	output = BytesIO()
	image.save(output, format="PNG")
	output.seek(0)
	return send_file(output, mimetype="image/png")


@app.route("/download-file/<cert_id>", methods=["GET"])
def download_file(cert_id: str):
	certificate_path = certificate_path_from_id(cert_id)
	if not os.path.exists(certificate_path):
		return render_template("index.html", error="Certificate file not found.", teams=load_team_names()), 404

	cert_name = request.args.get("cert_name", "")

	image = Image.open(certificate_path).convert("RGBA")
	draw = ImageDraw.Draw(image)
	width, height = image.size

	font = get_font(NAME_FONT_SIZE)
	draw.text((NAME_X, NAME_Y), cert_name, fill=NAME_COLOR, font=font, anchor="mm")

	output = BytesIO()
	image.save(output, format="PNG")
	output.seek(0)

	return send_file(
		output,
		mimetype="image/png",
		as_attachment=True,
		download_name=safe_download_name(cert_name),
	)


if __name__ == "__main__":
	app.run(debug=True)
