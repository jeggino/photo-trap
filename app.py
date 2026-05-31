import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import LocateControl, BeautifyIcon, MarkerCluster
from supabase import create_client, Client
from datetime import datetime
import uuid
import json
import re

# ----------------- CONFIG -----------------
st.set_page_config(
    page_title="Camera Trap Manager",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
[data-testid="stDialog"] {
    width: 90% !important;
    max-width: 1200px !important;
}
</style>
""", unsafe_allow_html=True)


SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

PROJECTS_TABLE = "project_members"
CAMERA_TABLE = "data_cameras"
SURVEY_TABLE = "survey_camera"
BOUNDARY_BUCKET = "observation_photos"   # optional, if you still use geojson per project
MEDIA_BUCKET = "camera_trap_media"

IMAGE = "https://www.nachtvandevleermuis.nl/wp-content/uploads/Elsken_Ecologie_LOGO-min-1024x748.png"
CROSS_IMAGE_PATH = "https://static.vecteezy.com/system/resources/previews/031/742/868/non_2x/transparent-circle-cross-icon-free-png.png"
OPACITY = 1
WIDTH = 30

marker_size = 28
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
@st.dialog("New Camera")
def new_camera_dialog():
    st.write("Use the map center as the camera position.")

    base_center = st.session_state.map_input_center
    zoom = 20

    m = folium.Map(location=base_center, zoom_start=zoom, zoom_control=False)
    LocateControl(auto_start=False).add_to(m)

    # Satellite layer (Esri)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite",
        overlay=False,
        control=True
    ).add_to(m)



    crosshair_html = f"""
    <div style='position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                pointer-events: none; z-index: 9999;'>
        <img src="{CROSS_IMAGE_PATH}" style="width:{WIDTH}px; opacity:{OPACITY};">
    </div>
    """
    m.get_root().html.add_child(folium.Element(crosshair_html))

    map_data = st_folium(m, width="100%", height=350)

    try:
        lat = map_data["center"]["lat"]
        lon = map_data["center"]["lng"]
    except Exception:
        lat, lon = base_center

    with st.expander("Camera details", expanded=True):
        camera_name = st.text_input("Camera name")
        camera_placed = st.date_input("Camera placed", value=datetime.utcnow().date())
        camera_moved = st.date_input("Camera moved (optional)",value=None)
        status = st.selectbox("Status", CAMERA_STATUS)
        comment = st.text_area("Comment")

    with st.expander("Camera photo (optional)"):
        photo = st.file_uploader("Upload a photo", type=["jpg", "jpeg", "png"])

    if st.button("Create camera", use_container_width=True):
    
        photo_url = None
        if photo:
            file_bytes = photo.getvalue()
            ext = photo.name.split(".")[-1].lower()
            file_id = f"{uuid.uuid4()}.{ext}"
    
            supabase.storage.from_(MEDIA_BUCKET).upload(
                file_id,
                file_bytes,
                file_options={"content-type": f"image/{ext}"}
            )
    
            photo_url = supabase.storage.from_(MEDIA_BUCKET).get_public_url(file_id)
    
        data = {
            "project": st.session_state.project,
            "observer": st.session_state.user.email,
            "camera_name": camera_name,
            "status": status,
            "comment": comment,
            "lat": float(lat),
            "lon": float(lon),
            "photo_url": photo_url,
            "camera_placed": camera_placed.isoformat(),
            "camera_moved": camera_moved.isoformat() if camera_moved else None,
        }
    
        supabase.table(CAMERA_TABLE).insert(data).execute()
    
        load_cameras(st.session_state.project)
        st.success("New camera created.")
        st.rerun()








@st.dialog("Manage Camera")
def manage_camera_dialog(camera):

    # Keep delete confirmation stable
    if "confirm_delete_camera" not in st.session_state:
        st.session_state.confirm_delete_camera = False

    st.subheader(f"Camera: {camera['camera_name']}")

    # ----------------- CAMERA DETAILS + POSITION -----------------
    with st.expander("Camera details", expanded=False):
        cam_name = st.text_input("Camera name", value=camera["camera_name"])

        # New fields: placed + moved
        try:
            placed = datetime.fromisoformat(camera.get("camera_placed", "")).date()
        except:
            placed = datetime.utcnow().date()

        try:
            moved = datetime.fromisoformat(camera.get("camera_moved", "")).date()
        except:
            moved = None

        camera_placed = st.date_input("Camera placed", value=placed)
        camera_moved = st.date_input("Camera moved", value=moved)

        status = st.selectbox("Status", CAMERA_STATUS, index=CAMERA_STATUS.index(camera["status"]))
        comment = st.text_area("Comment", value=camera.get("comment", ""))

        # -------- CHANGE CAMERA PHOTO --------
        st.markdown("### Camera photo")

        if camera.get("photo_url"):
            st.image(camera["photo_url"], width=250)

        new_cam_photo = st.file_uploader(
            "Replace camera photo",
            type=["jpg", "jpeg", "png"],
            key=f"new_cam_photo_{camera['id']}"
        )

        if new_cam_photo:
            # Delete old photo
            if camera.get("photo_url"):
                old_file = camera["photo_url"].split("/")[-1]
                try:
                    supabase.storage.from_(MEDIA_BUCKET).remove(old_file)
                except:
                    pass

            # Upload new photo
            file_bytes = new_cam_photo.getvalue()
            ext = new_cam_photo.name.split(".")[-1].lower()
            file_id = f"{uuid.uuid4()}.{ext}"

            supabase.storage.from_(MEDIA_BUCKET).upload(
                file_id,
                file_bytes,
                file_options={"content-type": f"image/{ext}"}
            )

            new_url = supabase.storage.from_(MEDIA_BUCKET).get_public_url(file_id)

            supabase.table(CAMERA_TABLE).update({
                "photo_url": new_url
            }).eq("id", camera["id"]).execute()

            st.success("Camera photo updated.")
            st.rerun()

        # -------- CAMERA POSITION --------
        st.markdown("### Camera position")

        edit_center = [camera["lat"], camera["lon"]]
        m = folium.Map(location=edit_center, zoom_start=18, zoom_control=False)

        # Satellite layer (Esri)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri World Imagery",
            name="Satellite",
            overlay=False,
            control=True
        ).add_to(m)
    

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
        <div style='position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                    pointer-events: none; z-index: 9999;'>
            <img src="{CROSS_IMAGE_PATH}" style="width:{WIDTH}px; opacity:{OPACITY};">
        </div>
        """
        m.get_root().html.add_child(folium.Element(crosshair_html))

        map_data = st_folium(m, width=350, height=350)

        try:
            new_lat = map_data["center"]["lat"]
            new_lon = map_data["center"]["lng"]
        except Exception:
            new_lat, new_lon = camera["lat"], camera["lon"]

        if st.button("Update camera", use_container_width=True):
            supabase.table(CAMERA_TABLE).update({
                "camera_name": cam_name,
                "status": status,
                "comment": comment,
                "lat": float(new_lat),
                "lon": float(new_lon),
                "camera_placed": str(camera_placed),
                "camera_moved": str(camera_moved) if camera_moved else None,
            }).eq("id", camera["id"]).execute()

            load_cameras(st.session_state.project)
            st.success("Camera updated.")
            st.rerun()

    st.divider()

    # ----------------- LIGHTBOX CSS/JS -----------------
    st.markdown("""
    <style>
    .lightbox-img {
        cursor: pointer;
        transition: 0.3s;
        border-radius: 6px;
    }
    .lightbox-img:hover {
        opacity: 0.8;
    }
    .lightbox-modal {
        display: none;
        position: fixed;
        z-index: 99999;
        padding-top: 60px;
        left: 0; top: 0;
        width: 100%; height: 100%;
        overflow: auto;
        background-color: rgba(0,0,0,0.9);
    }
    .lightbox-modal img {
        margin: auto;
        display: block;
        max-width: 90%;
        max-height: 90%;
    }
    </style>

    <script>
    function openLightbox(src) {
        var modal = document.getElementById("lightboxModal");
        var modalImg = document.getElementById("lightboxImage");
        modal.style.display = "block";
        modalImg.src = src;
    }
    function closeLightbox() {
        document.getElementById("lightboxModal").style.display = "none";
    }
    </script>

    <div id="lightboxModal" class="lightbox-modal" onclick="closeLightbox()">
        <img id="lightboxImage">
    </div>
    """, unsafe_allow_html=True)

    # ----------------- EXISTING SURVEYS -----------------
    st.markdown("### Camera surveys")

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
        for s in surveys:
            with st.expander(f"{s['date']} - {s.get('species','')}"):
                st.write(f"**Observer:** {s.get('observer','')}")
                st.write(f"**Species:** {s.get('species','')}")
                st.write(f"**Description:** {s.get('description','')}")

                urls = s.get("url_media") or []

                if urls:
                    st.markdown("#### Media gallery")

                    for url in urls:
                        ext = url.split(".")[-1].lower()

                        if ext in ["jpg", "jpeg", "png"]:
                            st.markdown(
                                f"""
                                <img src="{url}" class="lightbox-img" width="100%" onclick="openLightbox('{url}')">
                                """,
                                unsafe_allow_html=True
                            )

                        elif ext == "mp4":
                            st.video(url)

                if st.button(f"Delete survey {s['id']}", key=f"del_survey_{s['id']}"):
                    for url in urls:
                        filename = url.split("/")[-1]
                        try:
                            supabase.storage.from_(MEDIA_BUCKET).remove(filename)
                        except:
                            pass

                    supabase.table(SURVEY_TABLE).delete().eq("id", s["id"]).execute()
                    st.rerun()
    else:
        st.info("No surveys yet for this camera.")

    st.divider()

    # ----------------- ADD NEW SURVEY ENTRY -----------------
    with st.expander("Add new survey entry", expanded=False):
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

    # ----------------- DELETE CAMERA -----------------
    if not st.session_state.confirm_delete_camera:
        if st.button("Delete camera", type="secondary", use_container_width=True):
            st.session_state.confirm_delete_camera = True
            st.rerun()

    else:
        st.warning("Choose what to delete:")

        choice = st.radio(
            "Delete options:",
            [
                "Delete camera only",
                "Delete camera + survey photos",
                "Delete camera + camera photo + survey photos"
            ],
            key="delete_choice"
        )

        if st.button("Confirm delete", type="primary", use_container_width=True):

            # Delete camera photo
            if choice == "Delete camera + camera photo + survey photos":
                if camera.get("photo_url"):
                    filename = camera["photo_url"].split("/")[-1]
                    try:
                        supabase.storage.from_(MEDIA_BUCKET).remove(filename)
                    except:
                        pass

            # Delete survey photos
            if choice in [
                "Delete camera + survey photos",
                "Delete camera + camera photo + survey photos"
            ]:
                surveys = supabase.table(SURVEY_TABLE).select("*").eq("camera_name", camera["camera_name"]).eq("project", st.session_state.project).execute().data or []

                for s in surveys:
                    urls = s.get("url_media") or []
                    for url in urls:
                        filename = url.split("/")[-1]
                        try:
                            supabase.storage.from_(MEDIA_BUCKET).remove(filename)
                        except:
                            pass

                supabase.table(SURVEY_TABLE).delete().eq("camera_name", camera["camera_name"]).eq("project", st.session_state.project).execute()

            # Delete camera record
            supabase.table(CAMERA_TABLE).delete().eq("id", camera["id"]).execute()

            st.session_state.confirm_delete_camera = False
            load_cameras(st.session_state.project)
            st.success("Camera deleted.")
            st.rerun()

        if st.button("Cancel", use_container_width=True):
            st.session_state.confirm_delete_camera = False
            st.rerun()






# ----------------- MAP CENTER HELPER -----------------
def _get_center_from_map_data(map_data, fallback_center):
    try:
        center = map_data.get("center")
        if center and "lat" in center and "lng" in center:
            return [center["lat"], center["lng"]]
    except Exception:
        pass
    return fallback_center


# ----------------- MAIN APP -----------------
def show_main_app():
    # Top bar: only New Camera button (mobile-friendly)
    col1, col2 = st.columns([0.7, 0.3])
    with col1:
        st.write("")  # no title
    with col2:
        if st.button("New Camera", width="stretch", icon=":material/add_location_alt:"):
            new_camera_dialog()

    if st.sidebar.button("Change Project", width="stretch", icon=":material/sync_alt:"):
        st.session_state.changing_project = True
        st.rerun()

    if st.sidebar.button("Logout", width="stretch", icon=":material/login:"):
        logout()

    st.sidebar.divider()
    st.sidebar.header("Filters")

    cams = st.session_state.cameras

    # 1) Read previous selections
    prev_status = st.session_state.get("filter_status", [])

    # 2) Apply previous filters to get subset
    filtered_for_options = cams
    if prev_status:
        filtered_for_options = [c for c in filtered_for_options if c.get("status") in prev_status]

    # 3) Build available options
    status_options = sorted({c.get("status") for c in filtered_for_options if c.get("status")})

    prev_status = [s for s in prev_status if s in status_options]

    # 4) Render widgets
    selected_status = st.sidebar.multiselect(
        "Status",
        status_options,
        default=prev_status,
        key="filter_status"
    )

    # 5) Apply filters again to get final result
    filtered = cams
    if selected_status:
        filtered = [c for c in filtered if c.get("status") in selected_status]

    st.sidebar.divider()
    st.sidebar.header("Edit/Delete camera")

    # MAP
    m = folium.Map(location=st.session_state.map_center, zoom_start=12, zoom_control=False)
    LocateControl(auto_start=False).add_to(m)

    # Satellite layer (Esri)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite",
        overlay=False,
        control=True
    ).add_to(m)

    

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

        # Styled popup with photo support
        if cam.get("photo_url"):
            image_block = f"""
                <a href="{cam.get('photo_url')}" target="_blank">
                    <img src="{cam.get('photo_url')}" 
                         style="width: 100%; max-height: 120px; object-fit: cover; border-radius: 6px;">
                </a>
            """
        else:
            image_block = """
                <div style="
                    font-size: 22px;
                    text-align: center;
                    margin: 10px 0;
                ">📷</div>
            """
        
        popup_html = f"""
        <div style="
            background-color: white;
            padding: 10px 14px;
            border-radius: 10px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.25);
            font-family: 'Arial', sans-serif;
            width: 220px;
            border: 3px solid {color};
        ">
        
            <div style="font-weight: 700; font-size: 15px; color: {color}; margin-bottom: 6px; text-align: center;">
                {cam.get('camera_name','')}
            </div>
        
            <div style="text-align:center; margin-bottom:8px;">
                {image_block}
            </div>
        
            <div style="font-size: 12px; color: #444; margin-bottom: 4px;">
                Status: {cam.get('status','')}
            </div>
        
            <div style="font-size: 12px; color: #444; margin-bottom: 4px;">
                Placed: {cam.get('camera_placed','')}
            </div>
        
            <div style="font-size: 12px; color: #444; margin-bottom: 4px;">
                Moved: {cam.get('camera_moved','')}
            </div>
        
            <div style="font-size: 12px; color: #333; text-align: justify;">
                {cam.get('comment','')}
            </div>
        
        </div>
        """



        tooltip_text = str(cam["id"])

        marker_icon = icon

        folium.Marker(
            location=[cam["lat"], cam["lon"]],
            popup=popup_html,
            tooltip=tooltip_text,
            icon=marker_icon
        ).add_to(cluster)

    with st.container():
        st.markdown('<div class="fixed-map">', unsafe_allow_html=True)
        map_data = st_folium(m, height=450, width="100%")
        st.markdown('</div>', unsafe_allow_html=True)

    st.session_state.map_input_center = _get_center_from_map_data(map_data, st.session_state.map_center)

    if map_data and map_data.get("last_object_clicked_tooltip"):
        cam_id = map_data.get("last_object_clicked_tooltip")
        if cam_id:
            st.session_state.selected_camera_id = cam_id

    selected_id = st.session_state.selected_camera_id
    selected_cam = None
    for cam in filtered:
        if str(cam["id"]) == str(selected_id):
            selected_cam = cam
            break

    if selected_cam:
        cam_id = str(selected_cam["id"])
        label = f"({cam_id}) {selected_cam.get('camera_name','')} – {selected_cam.get('status','')}"
        if st.sidebar.button(label, key=f"cam_{cam_id}", width="stretch"):
            manage_camera_dialog(selected_cam)


# ----------------- RESTORE SESSION -----------------
def restore_session_after_functions():
    sess = supabase.auth.get_session()
    if sess and sess.user:
        st.session_state.logged_in = True
        st.session_state.user = sess.user
        st.session_state.session = sess

        metadata = sess.user.user_metadata or {}
        saved_project = metadata.get("project")

        if saved_project:
            st.session_state.project = saved_project
            load_cameras(saved_project)


restore_session_after_functions()


# ----------------- MAIN -----------------
def main():
    if not st.session_state.logged_in:
        if st.session_state.show_signup:
            show_signup()
        else:
            show_login()
    elif st.session_state.changing_project:
        show_project_selection()
        st.sidebar.divider()
        if st.sidebar.button("Logout", width="stretch", icon=":material/login:"):
            logout()
    elif not st.session_state.project:
        show_project_selection()
        st.sidebar.divider()
        if st.sidebar.button("Logout", width="stretch", icon=":material/login:"):
            logout()
    else:
        st.logo(IMAGE, link=None, size="large", icon_image=IMAGE)
        show_main_app()


if __name__ == "__main__":
    main()

