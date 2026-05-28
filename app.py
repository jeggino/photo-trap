import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import LocateControl, BeautifyIcon, MarkerCluster
from supabase import create_client, Client
from datetime import datetime, date
import uuid
import json
import pandas as pd
import re

# ----------------- CONFIG -----------------
st.set_page_config(
    page_title="Camera Trap Manager",
    layout="wide",
    initial_sidebar_state="expanded"
)

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

PROJECTS_TABLE = "project_members"
CAMERA_TABLE = "data_cameras"
SURVEY_TABLE = "survey_camera"
BOUNDARY_BUCKET = "observation_photos"   # optional, if you still use geojson per project
MEDIA_BUCKET = "camera_trap_media"

CROSS_IMAGE_PATH = "https://static.vecteezy.com/system/resources/previews/031/742/868/non_2x/transparent-circle-cross-icon-free-png.png"
OPACITY = 1
WIDTH = 30

IMAGE = "https://www.nachtvandevleermuis.nl/wp-content/uploads/Elsken_Ecologie_LOGO-min-1024x748.png"

marker_size = 25
inner_icon_px = 11

# ----------------- CAMERA STATUS -----------------
CAMERA_STATUS = ["active", "inactive"]

STATUS_COLORS = {
    "active": "green",
    "inactive": "gray",
}

# ----------------- INIT SUPABASE -----------------
@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

defaults = {
    "logged_in": False,
    "user": None,
    "session": None,
    "project": None,
    "changing_project": False,
    "cameras": [],
    "map_center": [52.0, 5.0],
    "map_input_center": [52.0, 5.0],
    "map_input_zoom": 6,
    "show_signup": False,
    "selected_camera_id": None,
    "filter_status": [],
}

for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ----------------- AUTH -----------------
def login(email: str, password: str):
    try:
        return supabase.auth.sign_in_with_password({"email": email, "password": password})
    except Exception:
        return None


def signup(email: str, password: str):
    try:
        return supabase.auth.sign_up({"email": email, "password": password})
    except Exception:
        return None


def logout():
    supabase.auth.sign_out()
    st.session_state.clear()
    for k, v in defaults.items():
        st.session_state[k] = v
    st.rerun()


# ----------------- DATA HELPERS -----------------
def load_projects():
    user = st.session_state.user
    if not user:
        return []
    res = (
        supabase
        .table(PROJECTS_TABLE)
        .select("project")
        .eq("user_id", user.id)
        .execute()
    )
    return res.data or []


def load_cameras(project_name: str):
    res = (
        supabase
        .table(CAMERA_TABLE)
        .select("*")
        .eq("project", project_name)
        .order("date", desc=False)
        .execute()
    )
    st.session_state.cameras = res.data or []

    if st.session_state.cameras:
        last = st.session_state.cameras[-1]
        st.session_state.map_center = [last["lat"], last["lon"]]
        st.session_state.map_input_center = [last["lat"], last["lon"]]


def load_project_boundary(project_name):
    """
    Load <project>.geojson from Supabase Storage (optional)
    and return (geojson_dict, bounds).
    """
    filename = f"{project_name}.geojson"

    try:
        file_bytes = supabase.storage.from_(BOUNDARY_BUCKET).download(filename)
        if not file_bytes:
            return None, None

        geojson_str = file_bytes.decode("utf-8")
        data = json.loads(geojson_str)

        coords = []

        def extract_coords(geom):
            t = geom["type"]
            c = geom["coordinates"]

            if t == "Polygon":
                for ring in c:
                    coords.extend(ring)
            elif t == "MultiPolygon":
                for poly in c:
                    for ring in poly:
                        coords.extend(ring)

        if data.get("type") == "Feature":
            extract_coords(data["geometry"])
        elif data.get("type") == "FeatureCollection":
            for feature in data["features"]:
                extract_coords(feature["geometry"])

        if not coords:
            return data, None

        lats = [p[1] for p in coords]
        lngs = [p[0] for p in coords]
        bounds = [[min(lats), min(lngs)], [max(lats), max(lngs)]]

        return data, bounds

    except Exception as e:
        st.warning(f"Could not load boundary for project '{project_name}': {e}")
        return None, None


# ----------------- STORAGE HELPERS -----------------
def upload_media_files(files):
    """
    Upload multiple files to MEDIA_BUCKET.
    Returns a list of public URLs.
    """
    if not files:
        return []

    urls = []
    for file in files:
        try:
            file_bytes = file.getvalue()
            if not file_bytes:
                continue

            ext = file.name.split(".")[-1].lower()
            file_id = f"{uuid.uuid4()}.{ext}"

            supabase.storage.from_(MEDIA_BUCKET).upload(
                file_id,
                file_bytes,
                file_options={"content-type": f"image/{ext}" if ext in ["jpg", "jpeg", "png"] else "video/mp4"}
            )

            url = supabase.storage.from_(MEDIA_BUCKET).get_public_url(file_id)
            urls.append(url)
        except Exception as e:
            st.error(f"Upload failed for {file.name}: {e}")
    return urls


# ----------------- UI: LOGIN -----------------
def show_login():
    st.sidebar.title("Login")

    with st.sidebar.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

        if submitted:
            res = login(email, password)
            if res and res.user:
                st.session_state.logged_in = True
                st.session_state.user = res.user
                st.session_state.session = res.session
                st.rerun()
            else:
                st.sidebar.error("Invalid email or password")

    if st.sidebar.button("Create Account"):
        st.session_state.show_signup = True
        st.rerun()


# ----------------- UI: SIGNUP -----------------
def show_signup():
    st.sidebar.title("Create Account")

    with st.sidebar.form("signup_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign Up")

        if submitted:
            res = signup(email, password)
            if res and res.user:
                st.sidebar.success("Account created. Please log in.")
                st.session_state.show_signup = False
                st.rerun()
            else:
                st.sidebar.error("Sign-up failed")

    if st.sidebar.button("Back to Login", use_container_width=True):
        st.session_state.show_signup = False
        st.rerun()


# ----------------- UI: PROJECT SELECT -----------------
def show_project_selection():
    st.sidebar.title("Select Project")

    res = (
        supabase.table(PROJECTS_TABLE)
        .select("project")
        .eq("user_id", st.session_state.user.id)
        .execute()
    )
    rows = res.data or []

    if not rows:
        st.sidebar.warning("You are not a member of any project.")
        return

    project_names = [row["project"] for row in rows]
    selected = st.sidebar.selectbox("Project", project_names)

    if st.sidebar.button("Confirm project", use_container_width=True):
        st.session_state.project = selected
        supabase.auth.update_user({"data": {"project": selected}})
        load_cameras(selected)
        st.session_state.changing_project = False
        st.rerun()


# ----------------- DIALOG: NEW CAMERA -----------------
@st.dialog("Insert a Camera")
def new_camera_dialog():
    st.write("Fill in the camera information.")

    camera_name = st.text_input("Camera name")
    cam_date = st.date_input("Date", value=datetime.utcnow().date())
    status = st.selectbox("Status", CAMERA_STATUS)
    comment = st.text_area("Comment")

    if st.button("Save camera", use_container_width=True):
        if not camera_name:
            st.error("Camera name is required.")
            st.stop()

        user_email = st.session_state.user.email
        project = st.session_state.project

        # Use current map center as camera position
        lat, lon = st.session_state.map_input_center

        data = {
            "camera_name": camera_name,
            "date": str(cam_date),
            "status": status,
            "comment": comment,
            "observer": user_email,
            "project": project,
            "lat": float(lat),
            "lon": float(lon),
        }

        supabase.table(CAMERA_TABLE).insert(data).execute()

        st.session_state.map_center = [float(lat), float(lon)]
        st.session_state.map_input_center = [float(lat), float(lon)]

        load_cameras(project)
        st.rerun()


# ----------------- DIALOG: MANAGE CAMERA -----------------
@st.dialog("Manage Camera")
def manage_camera_dialog(camera):
    st.subheader(f"Camera: {camera['camera_name']}")

    # --- EDIT CAMERA METADATA ---
    st.markdown("### Camera details")

    cam_name = st.text_input("Camera name", value=camera["camera_name"])
    try:
        d = datetime.fromisoformat(camera["date"]).date()
    except Exception:
        d = datetime.utcnow().date()
    cam_date = st.date_input("Date", value=d)
    status = st.selectbox("Status", CAMERA_STATUS, index=CAMERA_STATUS.index(camera["status"]))
    comment = st.text_area("Comment", value=camera.get("comment", ""))

    # Map to adjust coordinates
    st.markdown("### Camera position")
    edit_center = [camera["lat"], camera["lon"]]
    m = folium.Map(location=edit_center, zoom_start=18, zoom_control=False)
    LocateControl(auto_start=False).add_to(m)

    marker_icon = BeautifyIcon(
        icon="camera",
        icon_shape="marker",
        icon_anchor=[marker_size/2, marker_size],
        background_color=STATUS_COLORS.get(status, "blue"),
        border_color="black",
        border_width=0.7,
        text_color="white",
        icon_size=[marker_size, marker_size],
        inner_icon_style=(
            f"font-size:{inner_icon_px}px; display:flex; align-items:center; "
            f"justify-content:center; width:100%; height:100%; text-align:center; "
            f"padding:0; margin:0"
        )
    )

    folium.Marker(
        location=[camera["lat"], camera["lon"]],
        icon=marker_icon,
        popup="Current location"
    ).add_to(m)

    crosshair_html = f"""
    <div style="
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        pointer-events: none;
        z-index: 9999;
    ">
        <img src="{CROSS_IMAGE_PATH}"
             style="width:{WIDTH}px; opacity:{OPACITY};">
    </div>
    """
    m.get_root().html.add_child(folium.Element(crosshair_html))

    map_data = st_folium(m, width="100%", height=350)
    try:
        new_lat = map_data["center"]["lat"]
        new_lon = map_data["center"]["lng"]
    except Exception:
        new_lat, new_lon = camera["lat"], camera["lon"]

    if st.button("Update camera", use_container_width=True):
        supabase.table(CAMERA_TABLE).update({
            "camera_name": cam_name,
            "date": str(cam_date),
            "status": status,
            "comment": comment,
            "lat": float(new_lat),
            "lon": float(new_lon),
        }).eq("id", camera["id"]).execute()

        load_cameras(st.session_state.project)
        st.success("Camera updated.")
        st.rerun()

    st.divider()

    # --- SURVEY ENTRIES FOR THIS CAMERA ---
    st.markdown("### Camera surveys")

    # Load existing surveys
    res = (
        supabase.table(SURVEY_TABLE)
        .select("*")
        .eq("project", st.session_state.project)
        .eq("camera_name", camera["camera_name"])
        .order("date", desc=True)
        .execute()
    )
    surveys = res.data or []

    if surveys:
        st.write("Existing surveys:")
        for s in surveys:
            with st.expander(f"{s['date']} - {s.get('species','')}"):
                st.write(f"**Observer:** {s.get('observer','')}")
                st.write(f"**Species:** {s.get('species','')}")
                st.write(f"**Description:** {s.get('description','')}")
                urls = s.get("url_media") or []
                if urls:
                    st.write("Media URLs:")
                    for u in urls:
                        st.write(f"- {u}")

                if st.button(f"Delete survey {s['id']}", key=f"del_survey_{s['id']}"):
                    supabase.table(SURVEY_TABLE).delete().eq("id", s["id"]).execute()
                    st.experimental_rerun()
    else:
        st.info("No surveys yet for this camera.")

    st.markdown("### Add new survey entry")

    survey_date = st.date_input("Survey date", value=datetime.utcnow().date(), key=f"survey_date_{camera['id']}")
    species = st.text_input("Species", key=f"species_{camera['id']}")
    description = st.text_area("Description", key=f"desc_{camera['id']}")
    media_files = st.file_uploader(
        "Upload media (photos/videos)",
        type=["jpg", "jpeg", "png", "mp4"],
        accept_multiple_files=True,
        key=f"media_{camera['id']}"
    )

    if st.button("Save survey", use_container_width=True, key=f"save_survey_{camera['id']}"):
        observer = st.session_state.user.email
        project = st.session_state.project

        urls = upload_media_files(media_files)

        data = {
            "camera_name": camera["camera_name"],
            "observer": observer,
            "project": project,
            "date": str(survey_date),
            "species": species,
            "url_media": urls,
            "description": description,
        }

        supabase.table(SURVEY_TABLE).insert(data).execute()
        st.success("Survey saved.")
        st.rerun()

    st.divider()

    if st.button("Delete camera", type="secondary", use_container_width=True):
        # delete surveys for this camera
        supabase.table(SURVEY_TABLE).delete().eq("camera_name", camera["camera_name"]).eq("project", st.session_state.project).execute()
        # delete camera
        supabase.table(CAMERA_TABLE).delete().eq("id", camera["id"]).execute()
        load_cameras(st.session_state.project)
        st.rerun()


# ----------------- MAIN APP -----------------
def show_main_app():
    col1, col2 = st.columns([0.7, 0.3])
    with col1:
        st.image(IMAGE, width=150)
    with col2:
        if st.button("Insert a camera", use_container_width=True):
            new_camera_dialog()

    if st.sidebar.button("Change Project", use_container_width=True):
        st.session_state.changing_project = True
        st.rerun()

    if st.sidebar.button("Logout", use_container_width=True):
        logout()

    st.sidebar.divider()
    st.sidebar.header("Filters")

    cams = st.session_state.cameras

    # Status filter
    status_options = sorted({c.get("status") for c in cams if c.get("status")})
    prev_status = [s for s in st.session_state.get("filter_status", []) if s in status_options]

    selected_status = st.sidebar.multiselect(
        "Status",
        status_options,
        default=prev_status,
        key="filter_status"
    )

    filtered = cams
    if selected_status:
        filtered = [c for c in filtered if c.get("status") in selected_status]

    st.sidebar.divider()

    # MAP
    m = folium.Map(location=st.session_state.map_center, zoom_start=12, zoom_control=False)
    LocateControl(auto_start=False).add_to(m)

    # Optional: boundary
    if st.session_state.project:
        boundary, bounds = load_project_boundary(st.session_state.project)
        if boundary:
            folium.GeoJson(
                boundary,
                name="Boundary",
                style_function=lambda x: {
                    "fillColor": "#ffcc00",
                    "color": "red",
                    "weight": 2.5,
                    "fillOpacity": 0.1,
                }
            ).add_to(m)
            if bounds:
                m.fit_bounds(bounds)

    cluster = MarkerCluster().add_to(m)

    for cam in filtered:
        status = cam.get("status", "active")
        color = STATUS_COLORS.get(status, "blue")

        icon = BeautifyIcon(
            icon="camera",
            icon_shape="marker",
            icon_anchor=[marker_size/2, marker_size],
            background_color=color,
            border_color="black",
            border_width=0.7,
            text_color="white",
            icon_size=[marker_size, marker_size],
            inner_icon_style=(
                f"font-size:{inner_icon_px}px; display:flex; align-items:center; "
                f"justify-content:center; width:100%; height:100%; text-align:center; "
                f"padding:0; margin:0"
            )
        )

        popup_html = f"""
        <b>{cam['camera_name']}</b><br>
        Status: {cam['status']}<br>
        Date: {cam['date']}<br>
        <span style="display:none">{cam['id']}</span>
        """

        folium.Marker(
            location=[cam["lat"], cam["lon"]],
            icon=icon,
            popup=popup_html
        ).add_to(cluster)

    map_data = st_folium(m, width="100%", height=600)

    # Detect clicked marker via popup HTML
    if map_data and map_data.get("last_object_clicked_popup"):
        popup_html = map_data["last_object_clicked_popup"]
        match = re.search(r"<span style=\"display:none\">(.*?)</span>", popup_html)
        if match:
            cam_id = match.group(1)
            st.session_state.selected_camera_id = cam_id

    # If a camera is selected, open manage dialog
    if st.session_state.selected_camera_id:
        cam = next((c for c in st.session_state.cameras if c["id"] == st.session_state.selected_camera_id), None)
        if cam:
            if st.button("Manage observation", use_container_width=True):
                manage_camera_dialog(cam)


# ----------------- ENTRYPOINT -----------------
def main():
    if not st.session_state.logged_in:
        if st.session_state.show_signup:
            show_signup()
        else:
            show_login()
        return

    if not st.session_state.project or st.session_state.changing_project:
        show_project_selection()
        return

    show_main_app()


if __name__ == "__main__":
    main()
