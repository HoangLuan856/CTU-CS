"""
app.py - Hệ thống quản lý tài nguyên học tập bằng đồ thị tri thức
http://127.0.0.1:8050

Các file đi kèm:
  schema.sql: thiết kế bảng cơ sở dữ liệu
  db.py: làm việc với SQLite: đồ thị, người dùng, tài nguyên, phân quyền
  auth.py: đăng nhập / đăng ký / đăng xuất, bảo vệ các trang (Flask)
  storage.py: lưu file tài liệu người dùng tải lên (thư mục uploads/)
  learning.py: thuật toán tìm lộ trình học
  nodes.csv, edges.csv: dữ liệu đồ thị, tự đồng bộ vào DB mỗi lần chạy

Các trang sau khi đăng nhập:
  Đồ thị tri thức: chọn kiến thức -> lộ trình học; nút zoom tới node đã chọn
  Tài khoản: xem và sửa thông tin cá nhân, đổi mật khẩu
  Quản lý tài nguyên: thêm/ sửa/ xóa/tìm tài nguyên (quản trị viên)
  Quản lý người dùng: xem người dùng, đổi vai trò, khóa/ mở khóa, đặt lại mật khẩu, xóa (quản trị viên)
"""
from functools import lru_cache
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import Flask
import auth
import db
import learning
import storage

# 1. Lấy dữ liệu từ db
db.init_db()                                  
graph_nodes, EDGES = db.load_graph()           

NODE = {n["name"]: n for n in graph_nodes} # NODE["HTML"] = {"label", "type", "subject", ...}
all_nodes = [n["name"] for n in graph_nodes]
SUBJECTS = [n["name"] for n in graph_nodes if n["type"] == "Môn học"]
RANK = {name: i for i, name in enumerate(all_nodes)} # thứ tự gốc

LG = learning.LearningGraph(EDGES, RANK) # tìm lộ trình học
for _w in LG.warnings():                 
    print("[Cảnh báo dữ liệu]", _w)

# Các quan hệ
REL_TEXT = {
    "part_of":      {"fwd": "là 1 phần của",  "bwd": "bao gồm",           "color": "#B0BEC5"},
    "prerequisite": {"fwd": "là tiền đề của", "bwd": "cần học trước",     "color": "#E74C3C"},
    "uses":         {"fwd": "sử dụng",        "bwd": "được sử dụng bởi",  "color": "#F39C12"},
    "influences":   {"fwd": "ảnh hưởng đến",  "bwd": "chịu ảnh hưởng từ", "color": "#16A085"},
}

# Node
TYPE_STYLE = {
    "Môn học":        {"color": "#2D3436", "size": 26, "symbol": "diamond"},
    "Nhóm kiến thức": {"color": "#E17055", "size": 17, "symbol": "square"},
    "Chủ đề":         {"color": "#0984E3", "size": 12, "symbol": "circle"},
    "Khái niệm":      {"color": "#00B894", "size": 9,  "symbol": "circle"},
}
PALETTE = ["#2E86DE", "#10AC84", "#EE5A24", "#8E44AD", "#F39C12", "#2D3436"]
SUBJECT_COLOR = {s: PALETTE[i % len(PALETTE)] for i, s in enumerate(SUBJECTS)}
PATH_COLOR = "#6C5CE7" # màu làm nổi lộ trình học

# 2. Đồ thị
parent, children = {}, {}
for a, b, rel in EDGES:
    if rel == "part_of" and a not in parent: # mỗi node chỉ lấy 1 cha đầu tiên
        parent[a] = b
        children.setdefault(b, []).append(a)

for n in graph_nodes: # node không có cha -> gắn vào môn học
    if n["name"] not in parent and n["name"] not in SUBJECTS:
        print(f"[Cảnh báo dữ liệu] «{n['name']}» chưa thuộc nhóm nào ('là 1 phần của'), "
              f"tạm gắn vào môn «{n['subject']}».")
        parent[n["name"]] = n["subject"]
        children.setdefault(n["subject"], []).append(n["name"])

degree = {n: 0 for n in all_nodes} # số quan hệ của mỗi node
for a, b, rel in EDGES:
    degree[a] += 1
    degree[b] += 1

# Vị trí: x=cấp bậc, y=số thứ tự dòng
pos, next_row = {}, [0]

def place(node, depth):
    kids = children.get(node, [])
    if not kids:
        y = next_row[0]
        next_row[0] += 1
    else:
        ys = [place(k, depth + 1) for k in kids]
        y = (ys[0] + ys[-1]) / 2
    pos[node] = (depth, y)
    return y

for s in SUBJECTS:
    place(s, 0)
    next_row[0] += 1
TOTAL_ROWS = next_row[0]
ZOOM_ROWS = 24                                

def relations_of(node):
    """List (node_kia, loại quan hệ, câu mô tả) của 1 node"""
    result = []
    for a, b, rel in EDGES:
        if a == node:
            result.append((b, rel, REL_TEXT[rel]["fwd"]))
        elif b == node:
            result.append((a, rel, REL_TEXT[rel]["bwd"]))
    return result

@lru_cache(maxsize=512)
def get_path(selected):
    """Lộ trình học tới node `selected`, None nếu chưa chọn node"""
    return LG.make_path(selected) if selected in NODE else None

def zoom_view(nodes):
    """Khung nhìn (y_range, số dòng) vừa đủ chứa các node trong `nodes`, None nếu xem toàn bộ"""
    ys = [pos[n][1] for n in (nodes or []) if n in pos]
    if not ys:
        return None
    lo, hi = min(ys), max(ys)
    rows = min(max(ZOOM_ROWS, hi - lo + 4), TOTAL_ROWS)
    start = max(-0.5, min((lo + hi) / 2 - rows / 2, TOTAL_ROWS - 0.5 - rows))
    return [start + rows, start], rows      

# 3. Vẽ đồ thị
def arrow_trace(pairs, color, width, arrow_size, opacity=1.0, name=None):
    """Vẽ nhiều cạnh có hướng A -> B trong 1 trace
    Mỗi cạnh gồm 3 điểm và 1 điểm None để ngắt đoạn
    có marker hình mũi tên (size > 0); các điểm còn lại size = 0
    angleref="previous": mũi tên tự xoay theo hướng từ điểm trước tới điểm này"""
    xs, ys, sizes = [], [], []
    for a, b in pairs:
        (x0, y0), (x1, y1) = pos[a], pos[b]
        xs += [x0, (x0 + x1) / 2, x1, None]
        ys += [y0, (y0 + y1) / 2, y1, None]
        sizes += [0, arrow_size, 0, 0]
    return go.Scatter(
        x=xs, y=ys, mode="lines+markers", name=name, showlegend=name is not None,
        opacity=opacity, hoverinfo="skip", line=dict(color=color, width=width),
        marker=dict(symbol="arrow", size=sizes, color=color, angleref="previous"))

def make_figure(selected=None, zoom=None, path=None):
    fig = go.Figure()

    # lộ trình học
    path_order = (path or {}).get("order") or []
    has_path = len(path_order) > 1
    path_set = set(path_order) if has_path else set()
    related = set()
    if selected and not has_path: # không có lộ trình thì làm nổi các node liên quan trực tiếp
        related = {o for o, _, _ in relations_of(selected)} | {selected}
    focus = path_set | related | ({selected} if selected else set())
    dim = bool(focus) # có node nổi bật thì phần còn lại mờ đi

    # cạnh có hướng
    for rel in REL_TEXT:
        pairs = [(a, b) for a, b, r in EDGES if r == rel]
        fig.add_trace(arrow_trace(pairs, REL_TEXT[rel]["color"], 1, 8,
                                  opacity=0.12 if dim else 0.65, name=REL_TEXT[rel]["fwd"]))

    # chú thích cho màu node
    for name, st in TYPE_STYLE.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=name,
                                 marker=dict(size=11, color=st["color"], symbol=st["symbol"])))

    # cạnh của node đang chọn
    if selected and not has_path:
        for rel in REL_TEXT:
            pairs = [(a, b) for a, b, r in EDGES if r == rel and selected in (a, b)]
            if pairs:
                fig.add_trace(arrow_trace(pairs, REL_TEXT[rel]["color"], 3, 13))

    # cạnh của lộ trình học
    if has_path:
        path_pairs = [(a, b) for a, b in path["edges"] if a in pos and b in pos]
        if path_pairs:
            fig.add_trace(arrow_trace(path_pairs, PATH_COLOR, 4, 14))

    # node
    step = {n: i + 1 for i, n in enumerate(path_order)} if has_path else {}
    fills, sizes, symbols, borders, bwidths, opac = [], [], [], [], [], []
    texts, text_pos, text_col, hovers = [], [], [], []
    for n in all_nodes:
        info = NODE[n]
        st = TYPE_STYLE[info["type"]]        
        fill, size, border, bw = st["color"], st["size"], "white", 1

        if n in path_set:
            border, bw = PATH_COLOR, 4
        if n == selected:
            border, bw, size = "#000000", 4, size + 4

        fills.append(fill); sizes.append(size); symbols.append(st["symbol"])
        borders.append(border); bwidths.append(bw)
        opac.append(1 if (not dim or n in focus) else 0.2)

        texts.append(f"<b>{step[n]}. {n}</b>" if n in step else n)
        text_pos.append("top center" if n in children else "middle right")
        text_col.append("#2d3436" if (not dim or n in focus) else "#b2bec3")
        hovers.append(f"<b>{n}</b><br>{info['type']} · {info['subject']}<br>{degree[n]} quan hệ")

    fig.add_trace(go.Scatter(
        x=[pos[n][0] for n in all_nodes], y=[pos[n][1] for n in all_nodes],
        mode="markers+text", text=texts, textposition=text_pos,
        textfont=dict(size=12, color=text_col),
        customdata=all_nodes,                   # khi click, Dash nhận lại tên node từ đây
        hovertext=hovers, hoverinfo="text", showlegend=False,
        marker=dict(size=sizes, color=fills, symbol=symbols, opacity=opac,
                    line=dict(width=bwidths, color=borders))))

    # khung nhìn
    view = zoom_view((zoom or {}).get("nodes"))
    if view:
        y_range, rows = view
        height = int(rows * (32 if rows < TOTAL_ROWS else 28) + 110)
    else:
        y_range, height = [TOTAL_ROWS - 0.5, -0.5], int(TOTAL_ROWS * 28 + 110)

    fig.update_layout(
        height=height, plot_bgcolor="white",
        margin=dict(l=10, r=10, t=60, b=10),
        xaxis=dict(visible=False, range=[-0.7, 6]),
        yaxis=dict(visible=False, range=y_range),
        legend=dict(orientation="h", y=1.0, yanchor="bottom"),
        uirevision=f"v{(zoom or {}).get('token', 0)}")
    return fig

# 4. Khung lộ trình và tài liệu học
MUTED = {"color": "#636e72", "fontSize": 13}
CARD = {"background": "white", "border": "1px solid #dfe6e9", "borderRadius": 8,
        "padding": 12, "marginBottom": 12}

def chip(text, color):
    return html.Span(text, style={"background": color, "color": "white", "fontSize": 11,
                                  "padding": "1px 7px", "borderRadius": 10, "marginRight": 6})

def link(r):
    """Link tài nguyên: link ngoài và/hoặc file tải lên (/files/...), kèm vị trí cần xem."""
    anchors = []
    if r["url"]:
        anchors.append(html.A(r["title"], href=r["url"], target="_blank", rel="noopener noreferrer"))
    if r["file_path"]:
        text = f"📎 {r['file_name']}" if r["url"] else f"📎 {r['title']}"
        anchors.append(html.A(text, href=f"/files/{r['file_path']}", target="_blank", rel="noopener noreferrer"))
    out = []
    for i, a in enumerate(anchors):
        out += ["  ·  ", a] if i else [a]
    if r["note"]:
        out.append(html.Div(f"📍 {r['note']}", style=MUTED))
    return out

def render_info(selected):
    """Khung 'Thông tin node': loại, quan hệ, tài nguyên."""
    if not selected:
        return html.Div([
            html.H3("Chưa chọn node", style={"margin": "0 0 6px"}),
            html.Div(f"{len(all_nodes)} node · {len(EDGES)} quan hệ · "
                     f"{db.count_resources()} tài nguyên", style=MUTED),
            html.P("Click 1 node trên đồ thị: lộ trình học tới node đó hiện ở khung phía trên."),
        ])
    info = NODE[selected]
    rels = relations_of(selected)
    res = db.resources_of_node(selected)
    return html.Div([
        html.Div([chip(info["type"], TYPE_STYLE[info["type"]]["color"]),
                  chip(info["subject"], SUBJECT_COLOR[info["subject"]])]),
        html.H3(selected, style={"margin": "6px 0 2px"}),
        html.Div(f"Tên đầy đủ: {info['label']}", style=MUTED) if info["label"] != selected else None,
        html.H4(f"Quan hệ ({len(rels)})", style={"margin": "12px 0 4px"}),
        html.Ul([html.Li(f"{text}: {other}", style={"color": REL_TEXT[rel]["color"]})
                 for other, rel, text in rels], style={"margin": 0, "paddingLeft": 20}),
        html.H4(f"Tài nguyên học tập ({len(res)})", style={"margin": "12px 0 4px"}),
        *([html.Div([chip(r["type"], "#636e72"), *link(r)], style={"margin": "6px 0"}) for r in res]
          if res else [html.Div("Chưa có tài nguyên. Thêm ở trang «Quản lý tài nguyên»."
                   if db.can_manage_resources(auth.current_user())
                   else "Chưa có tài nguyên cho kiến thức này.", style=MUTED)]),
    ])

def render_path(path):
    """Khung 'Lộ trình học': các bước xếp theo giai đoạn"""
    if not path:
        return html.Div([
            html.H4("Lộ trình học", style={"margin": "0 0 6px", "color": PATH_COLOR}),
            html.Div("Chọn 1 kiến thức ở ô bên trên (hoặc click 1 node trên đồ thị) để xem cần học gì "
                     "trước nó, theo thứ tự nào.", style=MUTED)])
    target, order, layers, why = path["target"], path["order"], path["layers"], path["why"]
    res = db.resources_of_nodes(order)

    by_stage = {}                             
    for i, n in enumerate(order, 1):
        by_stage.setdefault(layers[n], []).append((i, n))

    blocks = []
    for stage in sorted(by_stage):
        items = by_stage[stage]
        title = f"Giai đoạn {stage + 1}" + ("  ·  các mục này học song song được" if len(items) > 1 else "")
        rows = []
        for i, n in items:
            is_target = n == target
            rows.append(html.Div([
                html.Div([
                    html.Span(str(i), style={"background": PATH_COLOR, "color": "white", "borderRadius": 10,
                                             "padding": "0 7px", "marginRight": 8, "fontSize": 12}),
                    html.B(n), "  ", chip(NODE[n]["type"], TYPE_STYLE[NODE[n]["type"]]["color"]),
                    chip("ĐÍCH", "#d63031") if is_target else None]),
                html.Div(why[n], style={**MUTED, "marginLeft": 28}) if n in why else None,
                *[html.Div(link(r), style={"marginLeft": 28, "fontSize": 13}) for r in res[n]],
            ], style={"padding": "6px 8px", "margin": "4px 0", "borderLeft": f"4px solid {PATH_COLOR}",
                      "background": "#e8e4ff" if is_target else "#f5f6fa"}))
        blocks.append(html.Div([html.Div(title, style={"fontWeight": 700, "fontSize": 13, "marginTop": 10}), *rows]))

    return html.Div([
        html.H4(f"Lộ trình học để nắm «{target}»", style={"margin": "0 0 6px", "color": PATH_COLOR}),
        html.Div(path["message"], style={"fontWeight": 600}),
        *blocks,
    ])

# 5. Giao diện
node_opts = [{"label": n, "value": n} for n in all_nodes]
type_opts = db.list_resource_types()
subject_opts = db.list_subjects()
LABEL = {"fontWeight": 600, "fontSize": 13, "display": "block", "marginBottom": 3}
FIELD = {"width": "100%", "boxSizing": "border-box", "padding": 6}
BTN = {"padding": "7px 12px", "cursor": "pointer"}
ADD_LABEL, SAVE_LABEL = "➕ Thêm tài nguyên", "💾 Lưu thay đổi"
ZOOM_LABEL, BACK_LABEL = "🔍 Zoom tới node đã chọn", "↺ Xem toàn bộ"
HIDDEN = {"display": "none"}
PAGE_GRAPH = {"label": " 🕸 Đồ thị tri thức", "value": "graph"}
PAGE_ACCOUNT = {"label": " 👤 Tài khoản", "value": "account"}
PAGE_RES = {"label": " 📚 Quản lý tài nguyên", "value": "res"} # chỉ admin
PAGE_ADMIN = {"label": " 🛠 Quản lý người dùng", "value": "admin"} # chỉ admin

def pages_for(user):
    """Các trang người dùng được thấy: người dùng chỉ có Đồ thị + Tài khoản."""
    if user and user["role"] == "admin":
        return [PAGE_GRAPH, PAGE_RES, PAGE_ADMIN, PAGE_ACCOUNT]
    return [PAGE_GRAPH, PAGE_ACCOUNT]

ROLE_COLOR = {"admin": "#d63031", "user": "#636e72"}

def field(label, component):
    return html.Div([html.Label(label, style=LABEL), component], style={"marginBottom": 10})

def ok(text):
    return html.Div(text, style={"color": "#00875a"})

def err(text):
    return html.Div(text, style={"color": "#d63031"})

# Flask
server = Flask(__name__)
auth.init_auth(server)
app = Dash(__name__, server=server)
app.title = "Đồ thị tri thức"

# Trang đồ thị
page_graph = html.Div(id="page-graph", children=[
    html.Div(style={"display": "flex", "gap": 12, "flexWrap": "wrap", "alignItems": "flex-end"}, children=[
        field("Chọn kiến thức (gõ chữ để tìm)",
              dcc.Dropdown(id="node-pick", options=node_opts, placeholder="Ví dụ: SVM", style={"width": 340})),
        html.Div(html.Button(ZOOM_LABEL, id="zoom-btn", n_clicks=0, style=BTN), style={"marginBottom": 10}),
    ]),
    html.Div(style={"display": "flex", "gap": 12, "alignItems": "flex-start"}, children=[
        html.Div(dcc.Graph(id="graph", config={"displaylogo": False}),
                 style={"flex": 3, "minWidth": 0, "height": "74vh", "overflowY": "auto",
                        "background": "white", "border": "1px solid #dfe6e9", "borderRadius": 8}),
        html.Div(style={"flex": 1, "minWidth": 340, "maxWidth": 480, "height": "74vh", "overflowY": "auto"},
                 children=[html.Div(id="path-panel", style=CARD), html.Div(id="info-panel", style=CARD)]),
    ]),
])

# Trang quản lý tài nguyên
page_res = html.Div(id="page-res", style=HIDDEN, children=[
    html.Div(style={"display": "flex", "gap": 12, "alignItems": "flex-start", "flexWrap": "wrap"}, children=[
        # --- form thêm / sửa tài nguyên ---
        html.Div(style={**CARD, "flex": 1, "minWidth": 330}, children=[
            html.H3("Thêm / sửa tài nguyên", style={"marginTop": 0}),
            field("Tiêu đề *", dcc.Input(id="f-title", type="text", value="", style=FIELD)),
            field("Mô tả", dcc.Textarea(id="f-desc", value="", style={**FIELD, "height": 60})),
            field("Loại tài nguyên *", dcc.Dropdown(id="f-type", options=type_opts)),
            field("Môn học *", dcc.Dropdown(id="f-subject", options=subject_opts)),
            field("Đường dẫn (link nơi lưu trữ)",
                  dcc.Input(id="f-url", type="text", value="", placeholder="https://...", style=FIELD)),
            field(f"hoặc tải file lên (tối đa {storage.MAX_UPLOAD_MB} MB)", html.Div([
                dcc.Upload(id="f-upload", multiple=False, max_size=storage.MAX_UPLOAD_MB * 1024 * 1024,
                           children=html.Div(["Kéo thả file vào đây hoặc ", html.A("chọn file")]),
                           style={"border": "1px dashed #b2bec3", "borderRadius": 6, "padding": 10,
                                  "textAlign": "center", "fontSize": 13, "background": "#fafafa"}),
                html.Div(id="upload-label", style={**MUTED, "marginTop": 4}),
                html.Div("Nhận: " + ", ".join(sorted(storage.ALLOWED_EXT)), style={**MUTED, "fontSize": 11}),
            ])),
            field("Node kiến thức liên quan * (chọn nhiều được)",
                  dcc.Dropdown(id="f-nodes", options=node_opts, multi=True, value=[])),
            field("Vị trí cần xem",
                  dcc.Textarea(id="f-note", value="", style={**FIELD, "height": 70},
                               placeholder="Chương 3, trang 45-60")),
            html.Div([html.Button(ADD_LABEL, id="add-btn", n_clicks=0, style=BTN),
                      html.Button("Hủy sửa", id="cancel-btn", n_clicks=0, style={**BTN, "marginLeft": 6})]),
            html.Div(id="res-msg", style={"marginTop": 8}),
        ]),
        # danh sách, tìm kiếm, sửa / xóa
        html.Div(style={**CARD, "flex": 2, "minWidth": 480}, children=[
            html.H3("Danh sách tài nguyên", style={"marginTop": 0}),
            html.Div(style={"display": "flex", "gap": 10, "flexWrap": "wrap", "alignItems": "center"}, children=[
                dcc.Input(id="res-search", type="text", value="", style={**FIELD, "width": 260},
                          placeholder="Tìm theo tiêu đề"),
                dcc.Dropdown(id="res-filter-type", options=type_opts, placeholder="Mọi loại", style={"width": 170}),
                dcc.Dropdown(id="res-filter-subject", options=subject_opts, placeholder="Mọi môn",
                             style={"width": 170}),
                dcc.Checklist(id="res-mine", options=[{"label": " Chỉ của tôi", "value": "mine"}], value=[]),
            ]),
            html.Div(id="res-table", style={"overflowX": "auto", "marginTop": 10}),
            html.Hr(),
            html.Div("Chọn một tài nguyên để sửa hoặc xóa:", style=MUTED),
            html.Div(style={"display": "flex", "gap": 10, "alignItems": "center", "flexWrap": "wrap",
                            "marginTop": 6}, children=[
                dcc.Dropdown(id="res-pick", placeholder="Chọn tài nguyên", style={"width": 360}),
                html.Button("✏ Sửa", id="edit-btn", n_clicks=0, style=BTN),
                dcc.ConfirmDialogProvider(id="del-confirm", message="Bạn chắc chắn muốn xóa tài nguyên này?",
                                          children=html.Button("🗑 Xóa", style=BTN)),
            ]),
        ]),
    ]),
])

# Trang tài khoản
page_account = html.Div(id="page-account", style=HIDDEN, children=[
    html.Div(style={"display": "flex", "gap": 12, "alignItems": "flex-start", "flexWrap": "wrap"}, children=[
        html.Div(style={**CARD, "flex": 1, "minWidth": 320, "maxWidth": 460}, children=[
            html.H3("Thông tin tài khoản", style={"marginTop": 0}),
            html.Div(id="acc-info", style={"marginBottom": 12, "fontSize": 14}),
            field("Họ và tên", dcc.Input(id="acc-fullname", type="text", value="", style=FIELD)),
            field("Email", dcc.Input(id="acc-email", type="text", value="", style=FIELD)),
            html.Button("Lưu thông tin", id="profile-btn", n_clicks=0, style=BTN),
            html.Div(id="profile-msg", style={"marginTop": 8}),
        ]),
        html.Div(style={**CARD, "flex": 1, "minWidth": 320, "maxWidth": 460}, children=[
            html.H3("Đổi mật khẩu", style={"marginTop": 0}),
            field("Mật khẩu hiện tại", dcc.Input(id="pw-old", type="password", value="", style=FIELD)),
            field("Mật khẩu mới (ít nhất 8 ký tự, có chữ và số)",
                  dcc.Input(id="pw-new", type="password", value="", style=FIELD)),
            field("Nhập lại mật khẩu mới", dcc.Input(id="pw-new2", type="password", value="", style=FIELD)),
            html.Button("Đổi mật khẩu", id="pw-btn", n_clicks=0, style=BTN),
            html.Div(id="pw-msg", style={"marginTop": 8}),
            html.Div("Đổi xong bạn sẽ phải đăng nhập lại", style=MUTED),
        ]),
    ]),
])

# Trang quản lý người dùng
page_admin = html.Div(id="page-admin", style=HIDDEN, children=[
    html.Div(style=CARD, children=[
        html.H3("Quản lý người dùng", style={"marginTop": 0}),
        html.Div(id="users-table", style={"overflowX": "auto"}),
        html.Hr(),
        html.Div(style={"display": "flex", "gap": 10, "alignItems": "center", "flexWrap": "wrap"}, children=[
            dcc.Dropdown(id="user-pick", placeholder="Chọn người dùng", style={"width": 300}),
            dcc.Dropdown(id="role-pick", placeholder="Vai trò mới", style={"width": 170},
                         options=[{"label": db.ROLE_LABEL[r], "value": r} for r in db.ROLES]),
            html.Button("Đổi vai trò", id="role-btn", n_clicks=0, style=BTN),
            html.Button("🔒 Khóa / mở khóa", id="active-btn", n_clicks=0, style=BTN),
            dcc.ConfirmDialogProvider(id="user-del-confirm", children=html.Button("🗑 Xóa người dùng", style=BTN),
                                      message="Xóa tài khoản này? (Tài nguyên họ đã thêm vẫn được giữ lại.)"),
        ]),
        html.Div(style={"display": "flex", "gap": 10, "alignItems": "center", "marginTop": 10}, children=[
            dcc.Input(id="newpw", type="password", value="", style={**FIELD, "width": 300},
                      placeholder="Mật khẩu mới cho người đã chọn (8+ ký tự, chữ và số)"),
            html.Button("Đặt lại mật khẩu", id="resetpw-btn", n_clicks=0, style=BTN),
        ]),
        html.Div(id="users-msg", style={"marginTop": 8}),
    ]),
])

app.layout = html.Div(style={"fontFamily": "Arial", "padding": 12, "background": "#f5f6fa"}, children=[
    dcc.Location(id="url", refresh=True),      
    html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
                    "flexWrap": "wrap", "gap": 8}, children=[
        html.H2("Quản lý tài nguyên học tập bằng đồ thị tri thức", style={"margin": "0 0 6px"}),
        html.Div(id="user-box"),
    ]),
    dcc.RadioItems(id="page", value="graph", options=pages_for(None), inline=True, style={"marginBottom": 10},
                   inputStyle={"marginLeft": 14, "marginRight": 4}),
    page_graph, page_res, page_account, page_admin,
    # Các kho nhớ lưu trạng thái dùng chung 
    dcc.Store(id="sel"),                       
    dcc.Store(id="zoom"),                      
    dcc.Store(id="res-version", data=0),       
    dcc.Store(id="editing-id"),                 
    dcc.Store(id="account-version", data=0),     
    dcc.Store(id="users-version", data=0),       
])

# 6. CALLBACK: nối giao diện với Python
NU = no_update

@app.callback(Output("page-graph", "style"), Output("page-res", "style"),
              Output("page-account", "style"), Output("page-admin", "style"), Input("page", "value"))
def switch_page(page):
    return tuple({"display": "block"} if page == p else HIDDEN for p in ("graph", "res", "account", "admin"))

# Góc phải trên, trang Tài khoản, danh sách trang (chỉ admin thấy 2 trang quản lý)
@app.callback(
    Output("user-box", "children"), Output("page", "options"),
    Output("acc-info", "children"), Output("acc-fullname", "value"), Output("acc-email", "value"),
    Input("url", "pathname"), Input("account-version", "data"))
def render_header(_path, _version):
    user = auth.current_user()
    if not user:
        return html.A("Đăng nhập", href="/login"), pages_for(None), "", "", ""
    options = pages_for(user)
    box = html.Div([
        html.Span(f"👤 {user['full_name'] or user['username']}", style={"fontWeight": 600, "marginRight": 8}),
        chip(db.ROLE_LABEL[user["role"]], ROLE_COLOR[user["role"]]),
        html.A("Đăng xuất", href="/logout", style={"marginLeft": 8}),
    ])
    info = html.Div([
        html.Div([html.B("Tên đăng nhập: "), user["username"]]),
        html.Div([html.B("Vai trò: "), db.ROLE_LABEL[user["role"]]]),
        html.Div([html.B("Tham gia: "), user["created_at"]]),
        html.Div([html.B("Đăng nhập lần trước: "), user["last_login"] or "—"]),
    ])
    return box, options, info, user["full_name"], user["email"]

# Chọn node (click đồ thị hoặc ô chọn kiến thức)/ bấm nút zoom
def zoomed_on(zoom, node):
    return bool(zoom) and zoom.get("nodes") == [node]

@app.callback(
    Output("sel", "data"), Output("zoom", "data"), Output("zoom-btn", "children"),
    Input("graph", "clickData"), Input("node-pick", "value"), Input("zoom-btn", "n_clicks"),
    State("sel", "data"), State("zoom", "data"),
    prevent_initial_call=True)
def navigate(click_data, picked, _zoom_clicks, sel, zoom):
    trig = ctx.triggered_id                      
    new_sel, new_zoom = sel, zoom

    if trig == "graph": # click vào 1 node
        points = (click_data or {}).get("points") or [{}]
        node = points[0].get("customdata")
        if node in NODE:
            new_sel = node
    elif trig == "node-pick": # chọn trong ô chọn kiến thức
        if picked in NODE:
            new_sel = picked
    elif trig == "zoom-btn" and new_sel in NODE:
        token = (zoom or {}).get("token", 0) + 1   
        if zoomed_on(zoom, new_sel): # bỏ zoom
            new_zoom = {"nodes": [], "token": token}
        else:
            new_zoom = {"nodes": [new_sel], "token": token}

    label = BACK_LABEL if zoomed_on(new_zoom, new_sel) else ZOOM_LABEL
    return (NU if new_sel == sel else new_sel), (NU if new_zoom is zoom else new_zoom), label

# Vẽ đồ thị và các khung lộ trình
@app.callback(Output("graph", "figure"), Input("sel", "data"), Input("zoom", "data"))
def render_graph(sel, zoom):
    sel = sel if sel in NODE else None
    return make_figure(sel, zoom, get_path(sel))

@app.callback(
    Output("path-panel", "children"), Output("info-panel", "children"),
    Input("sel", "data"), Input("res-version", "data"))
def render_side(sel, _version):
    sel = sel if sel in NODE else None
    return render_path(get_path(sel)), render_info(sel)

# Thêm/ sửa/ xóa tài nguyên
OUT = ["msg", "version", "title", "desc", "type", "subject", "url", "nodes", "note", "pick", "editing", "btn",
       "up_contents", "up_filename"]

def result(**kw):
    return tuple(kw.get(k, NU) for k in OUT)

CLEAR = dict(title="", desc="", type=None, subject=None, url="", nodes=[], note="",
             editing=None, btn=ADD_LABEL, up_contents=None, up_filename=None)

@app.callback(Output("upload-label", "children"), Input("f-upload", "filename"))
def show_upload_name(filename):
    return f"📎 Đã chọn: {filename}" if filename else ""

@app.callback(
    Output("res-msg", "children"), Output("res-version", "data"),
    Output("f-title", "value"), Output("f-desc", "value"), Output("f-type", "value"),
    Output("f-subject", "value"), Output("f-url", "value"), Output("f-nodes", "value"),
    Output("f-note", "value"), Output("res-pick", "value"), Output("editing-id", "data"),
    Output("add-btn", "children"), Output("f-upload", "contents"), Output("f-upload", "filename"),
    Input("add-btn", "n_clicks"), Input("cancel-btn", "n_clicks"),
    Input("edit-btn", "n_clicks"), Input("del-confirm", "submit_n_clicks"),
    State("f-title", "value"), State("f-desc", "value"), State("f-type", "value"),
    State("f-subject", "value"), State("f-url", "value"), State("f-nodes", "value"),
    State("f-note", "value"), State("f-upload", "contents"), State("f-upload", "filename"),
    State("res-pick", "value"), State("editing-id", "data"), State("res-version", "data"),
    prevent_initial_call=True)
def manage_resources(_add, _cancel, _edit, _del, title, desc, rtype, subject, url, nodes, note,
                     up_contents, up_filename, pick, editing, version):
    version = version or 0
    trig = ctx.triggered_id
    user = auth.current_user()
    if not user:
        return result(msg=err("Phiên đăng nhập đã hết hạn. Hãy tải lại trang và đăng nhập lại."))
    if not db.can_manage_resources(user):      
        return result(msg=err(db.RESOURCE_ADMIN_ONLY))

    if trig == "add-btn":                       
        links = db.parse_note_lines(note, nodes or [])
        new_file = None
        if up_contents:                     
            try:
                new_file = storage.save_upload(up_contents, up_filename)
            except ValueError as e:
                return result(msg=err(str(e)))
        try:
            if editing is not None:
                old_file = db.update_resource(editing, title, desc, rtype, subject, url, links,
                                              actor=user, new_file=new_file)
                storage.delete_file(old_file) # file cũ bị thay
                text = f"Đã cập nhật tài nguyên #{editing}."
            else:
                rid = db.add_resource(title, desc, rtype, subject, url, links, actor=user,
                                      file_path=new_file[0] if new_file else "",
                                      file_name=new_file[1] if new_file else "")
                text = f"Đã thêm tài nguyên #{rid}."
        except (ValueError, PermissionError) as e: # nhập thiếu/ sai/ không có quyền -> báo lỗi
            if new_file:
                storage.delete_file(new_file[0])  
            return result(msg=err(str(e)))
        return result(msg=ok(text), version=version + 1, pick=None, **CLEAR)

    if trig == "cancel-btn":
        return result(msg=ok("Đã hủy sửa."), **CLEAR)

    if trig == "edit-btn": # nạp tài nguyên đã chọn lên form
        r = db.get_resource(pick) if pick is not None else None
        if r is None:
            return result(msg=err("Hãy chọn tài nguyên cần sửa."))
        extra = f" Đang có file đính kèm «{r['file_name']}» (giữ nguyên nếu bạn không chọn file mới)." \
            if r["file_path"] else ""
        return result(msg=ok(f"Đang sửa tài nguyên #{r['id']}: chỉnh xong bấm «Lưu thay đổi».{extra}"),
                      title=r["title"], desc=r["description"], type=r["type"], subject=r["subject"],
                      url=r["url"], nodes=list(r["links"]), note=db.format_notes(r["links"]),
                      editing=r["id"], btn=SAVE_LABEL, up_contents=None, up_filename=None)

    if trig == "del-confirm":
        if pick is None:
            return result(msg=err("Hãy chọn tài nguyên cần xóa."))
        try:
            storage.delete_file(db.delete_resource(pick, user))
        except (ValueError, PermissionError) as e:
            return result(msg=err(str(e)))
        extra = CLEAR if editing == pick else {}  
        return result(msg=ok(f"Đã xóa tài nguyên #{pick}."), version=version + 1, **{**extra, "pick": None})
    return result()

# Bảng danh sách tài nguyên, ô chọn tài nguyên để sửa / xóa
@app.callback(
    Output("res-table", "children"), Output("res-pick", "options"),
    Input("res-search", "value"), Input("res-filter-type", "value"), Input("res-filter-subject", "value"),
    Input("res-mine", "value"), Input("res-version", "data"))
def render_res_table(keyword, type_name, subject_name, mine, _version):
    user = auth.current_user()
    if not db.can_manage_resources(user):
        return html.Div(db.RESOURCE_ADMIN_ONLY, style=MUTED), []
    owner = user["id"] if "mine" in (mine or []) else None
    rows = db.search_resources(keyword or "", type_name, subject_name, owner)
    th = {"textAlign": "left", "padding": "6px 8px", "borderBottom": "2px solid #dfe6e9", "fontSize": 13}
    td = {"padding": "6px 8px", "borderBottom": "1px solid #f1f2f6", "fontSize": 14, "verticalAlign": "top"}
    table = html.Table(style={"borderCollapse": "collapse", "width": "100%"}, children=[
        html.Thead(html.Tr([html.Th(h, style=th) for h in
                            ["ID", "Tiêu đề (bấm để mở)", "Loại", "Môn", "Node liên quan (vị trí cần xem)",
                             "Người thêm", "Ngày thêm"]])),
        html.Tbody([html.Tr([
            html.Td(r["id"], style=td),
            html.Td(link({**r, "note": ""}), style=td),
            html.Td(r["type"], style=td), html.Td(r["subject"], style=td),
            html.Td(r["nodes"], style=td),
            html.Td(r["owner"] or "(không rõ)", style=td), html.Td(r["created_at"][:10], style=td),
        ]) for r in rows]),
    ])
    body = table if rows else html.Div("Không có tài nguyên nào phù hợp.", style=MUTED)
    options = [{"label": f"#{r['id']} - {r['title']}", "value": r["id"]}
               for r in db.search_resources("")]
    return html.Div([html.Div(f"{len(rows)} tài nguyên", style=MUTED), body]), options

# Trang Tài khoản: lưu hồ sơ, đổi mật khẩu
@app.callback(
    Output("profile-msg", "children"), Output("account-version", "data"),
    Input("profile-btn", "n_clicks"),
    State("acc-fullname", "value"), State("acc-email", "value"), State("account-version", "data"),
    prevent_initial_call=True)
def save_profile(_n, fullname, email, version):
    user = auth.current_user()
    if not user:
        return err("Phiên đăng nhập đã hết hạn."), NU
    try:
        db.update_profile(user["id"], fullname, email)
    except ValueError as e:
        return err(str(e)), NU
    return ok("Đã lưu thông tin."), (version or 0) + 1

@app.callback(
    Output("pw-msg", "children"), Output("url", "href"),
    Input("pw-btn", "n_clicks"),
    State("pw-old", "value"), State("pw-new", "value"), State("pw-new2", "value"),
    prevent_initial_call=True)
def change_my_password(_n, old, new, new2):
    user = auth.current_user()
    if not user:
        return err("Phiên đăng nhập đã hết hạn."), NU
    if new != new2:
        return err("Hai mật khẩu mới không khớp."), NU
    try:
        db.change_password(user["id"], old, new)
    except ValueError as e:
        return err(str(e)), NU
    return ok("Đã đổi mật khẩu. Đang chuyển về trang đăng nhập..."), "/logout"   # phiên cũ đã mất hiệu lực

# Trang Quản trị
NOT_ADMIN = "Chỉ quản trị viên mới xem được trang này."

@app.callback(
    Output("users-table", "children"), Output("user-pick", "options"),
    Input("users-version", "data"), Input("url", "pathname"))
def render_users(_version, _path):
    user = auth.current_user()
    if not user or user["role"] != "admin":
        return html.Div(NOT_ADMIN, style=MUTED), []
    users = db.list_users()
    th = {"textAlign": "left", "padding": "6px 8px", "borderBottom": "2px solid #dfe6e9", "fontSize": 13}
    td = {"padding": "6px 8px", "borderBottom": "1px solid #f1f2f6", "fontSize": 14, "verticalAlign": "top"}
    table = html.Table(style={"borderCollapse": "collapse", "width": "100%"}, children=[
        html.Thead(html.Tr([html.Th(h, style=th) for h in
                            ["ID", "Tên đăng nhập", "Họ tên", "Email", "Vai trò", "Trạng thái",
                             "Tài nguyên", "Đăng nhập cuối"]])),
        html.Tbody([html.Tr([
            html.Td(u["id"], style=td), html.Td(u["username"], style=td), html.Td(u["full_name"], style=td),
            html.Td(u["email"], style=td),
            html.Td(chip(db.ROLE_LABEL[u["role"]], ROLE_COLOR[u["role"]]), style=td),
            html.Td("Hoạt động" if u["is_active"] else "🔒 Bị khóa",
                    style={**td, "color": "#00875a" if u["is_active"] else "#d63031"}),
            html.Td(u["n_resources"], style=td), html.Td(u["last_login"] or "—", style=td),
        ]) for u in users]),
    ])
    options = [{"label": f"#{u['id']} - {u['username']} ({db.ROLE_LABEL[u['role']]})", "value": u["id"]}
               for u in users]
    return html.Div([html.Div(f"{len(users)} người dùng · {db.count_resources()} tài nguyên", style=MUTED),
                     table]), options

@app.callback(
    Output("users-msg", "children"), Output("users-version", "data"),
    Output("newpw", "value"), Output("user-pick", "value"),
    Input("role-btn", "n_clicks"), Input("active-btn", "n_clicks"),
    Input("resetpw-btn", "n_clicks"), Input("user-del-confirm", "submit_n_clicks"),
    State("user-pick", "value"), State("role-pick", "value"), State("newpw", "value"),
    State("users-version", "data"),
    prevent_initial_call=True)
def manage_users(_r, _a, _p, _d, pick, role, newpw, version):
    version = version or 0
    trig = ctx.triggered_id
    admin = auth.current_user()
    if not admin or admin["role"] != "admin":
        return err(NOT_ADMIN), NU, NU, NU
    if pick is None:
        return err("Hãy chọn người dùng ở ô phía trên."), NU, NU, NU
    try:
        if trig == "role-btn":
            if role is None:
                return err("Hãy chọn vai trò mới."), NU, NU, NU
            db.set_user_role(pick, role, admin["id"])
            text = f"Đã đổi vai trò của #{pick} thành «{db.ROLE_LABEL[role]}»."
        elif trig == "active-btn":
            target = db.get_user(pick)
            if target is None:
                return err("Người dùng không còn tồn tại."), NU, NU, NU
            db.set_user_active(pick, not target["is_active"], admin["id"])
            text = f"Đã {'mở khóa' if not target['is_active'] else 'khóa'} tài khoản #{pick}."
        elif trig == "resetpw-btn":
            db.admin_set_password(pick, newpw, admin["id"])
            text = f"Đã đặt lại mật khẩu của #{pick} (người đó phải đăng nhập lại)."
        elif trig == "user-del-confirm":
            db.delete_user(pick, admin["id"])
            return ok(f"Đã xóa tài khoản #{pick}."), version + 1, "", None
        else:
            return NU, NU, NU, NU
    except (ValueError, PermissionError) as e:
        return err(str(e)), NU, NU, NU
    return ok(text), version + 1, "", NU

if __name__ == "__main__":
    app.run(debug=True)