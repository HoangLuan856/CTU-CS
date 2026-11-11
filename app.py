"""Quản lý tài nguyên học tập bằng đồ thị tri thức"""

from functools import lru_cache
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import Flask
import auth
import db
import learning
import storage

# 1. Dữ liệu đồ thị
db.init_db()                                    
graph_nodes, EDGES = db.load_graph()           
NODE = {n["name"]: n for n in graph_nodes}    
all_nodes = list(NODE)
SUBJECTS = [n for n in all_nodes if NODE[n]["type"] == "Môn học"]
LG = learning.LearningGraph(EDGES, {n: i for i, n in enumerate(all_nodes)})

# Cách đọc mỗi loại quan hệ
REL_TEXT = {
    "part_of":      {"fwd": "là 1 phần của",  "bwd": "bao gồm",           "color": "#B0BEC5"},
    "prerequisite": {"fwd": "là tiền đề của", "bwd": "cần học trước",     "color": "#E74C3C"},
    "uses":         {"fwd": "sử dụng",        "bwd": "được sử dụng bởi",  "color": "#F39C12"},
    "influences":   {"fwd": "ảnh hưởng đến",  "bwd": "chịu ảnh hưởng từ", "color": "#16A085"},
}
# Kiểu hiển thị node theo loại
TYPE_STYLE = {
    "Môn học":        {"color": "#2D3436", "size": 26, "symbol": "diamond"},
    "Nhóm kiến thức": {"color": "#E17055", "size": 17, "symbol": "square"},
    "Chủ đề":         {"color": "#0984E3", "size": 12, "symbol": "circle"},
    "Khái niệm":      {"color": "#00B894", "size": 9,  "symbol": "circle"},
}
PATH_COLOR = "#6C5CE7" # màu làm nổi lộ trình học

# 2. Cây và node
parent, children = {}, {}
for a, b, rel in EDGES:
    if rel == "part_of" and a not in parent: # mỗi node chỉ lấy 1 cha đầu tiên
        parent[a] = b
        children.setdefault(b, []).append(a)
for n in all_nodes: # node quên khai báo "là 1 phần" -> gắn vào môn học
    if n not in parent and n not in SUBJECTS:
        parent[n] = NODE[n]["subject"]
        children.setdefault(NODE[n]["subject"], []).append(n)

pos, next_row = {}, [0]

def place(node, depth):
    kids = children.get(node, [])
    if kids:
        ys = [place(k, depth + 1) for k in kids]
        y = (ys[0] + ys[-1]) / 2
    else:
        y = next_row[0]
        next_row[0] += 1
    pos[node] = (depth, y)
    return y

for s in SUBJECTS:
    place(s, 0)
    next_row[0] += 1
TOTAL_ROWS = next_row[0]
ZOOM_ROWS = 24                                 

def relations_of(node):
    return ([(b, rel, REL_TEXT[rel]["fwd"]) for a, b, rel in EDGES if a == node] +
            [(a, rel, REL_TEXT[rel]["bwd"]) for a, b, rel in EDGES if b == node])

@lru_cache(maxsize=512)
def get_path(selected):
    """Lộ trình học tới node được chọn, none nếu chưa chọn node."""
    return LG.make_path(selected) if selected in NODE else None

def zoom_range(node):
    """Khoảng trục y"""
    start = max(-0.5, min(pos[node][1] - ZOOM_ROWS / 2, TOTAL_ROWS - 0.5 - ZOOM_ROWS))
    return [start + ZOOM_ROWS, start]

def zoomed_on(zoom, node):
    """Đồ thị có đang zoom đúng không?"""
    return node is not None and bool(zoom) and zoom.get("node") == node

# 3. Vẽ đồ thị
def arrow_trace(pairs, color, width, arrow_size, opacity=1.0, name=None):
    """Vẽ các cạnh có hướng A -> B trong 1 trace, mỗi cạnh gồm [đầu A, mũi tên, đầu B, None],
    marker mũi tên (size > 0), angleref="previous" làm mũi tên tự xoay theo chiều A -> B."""
    xs, ys, sizes = [], [], []
    for a, b in pairs:
        (x0, y0), (x1, y1) = pos[a], pos[b]
        xs += [x0, (x0 + x1) / 2, x1, None]
        ys += [y0, (y0 + y1) / 2, y1, None]
        sizes += [0, arrow_size, 0, 0]
    return go.Scatter(x=xs, y=ys, mode="lines+markers", name=name, showlegend=name is not None,
                      opacity=opacity, hoverinfo="skip", line=dict(color=color, width=width),
                      marker=dict(symbol="arrow", size=sizes, color=color, angleref="previous"))

def make_figure(selected=None, zoom=None, path=None):
    fig = go.Figure()
    path_order = (path or {}).get("order") or []
    has_path = len(path_order) > 1 # chỉ coi là "có lộ trình" khi có từ 2 node
    path_set = set(path_order) if has_path else set()
    related = {selected, *(o for o, _, _ in relations_of(selected))} if selected and not has_path else set()
    focus = path_set | related | ({selected} if selected else set()) # các node được làm nổi bật
    dim = bool(focus) # có node nổi bật thì phần còn lại mờ đi

    for rel, style in REL_TEXT.items(): # cạnh có hướng, mỗi loại quan hệ 1 trace
        pairs = [(a, b) for a, b, r in EDGES if r == rel]
        fig.add_trace(arrow_trace(pairs, style["color"], 1, 8, 0.12 if dim else 0.65, style["fwd"]))
    for name, st in TYPE_STYLE.items(): # chú thích loại node
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=name,
                                 marker=dict(size=11, color=st["color"], symbol=st["symbol"])))
    if selected and not has_path: # cạnh của node đang chọn: vẽ đậm
        for rel, style in REL_TEXT.items():
            pairs = [(a, b) for a, b, r in EDGES if r == rel and selected in (a, b)]
            if pairs:
                fig.add_trace(arrow_trace(pairs, style["color"], 3, 13))
    if has_path: # cạnh của lộ trình: màu tím, mũi tên chỉ chiều HỌC
        fig.add_trace(arrow_trace([tuple(e) for e in path["edges"]], PATH_COLOR, 4, 14))
    step = {n: i + 1 for i, n in enumerate(path_order)} if has_path else {}
    sizes, borders, widths, opacity, texts, text_color = [], [], [], [], [], []
    for n in all_nodes:
        size, border, width = TYPE_STYLE[NODE[n]["type"]]["size"], "white", 1
        if n in path_set:
            border, width = PATH_COLOR, 4
        if n == selected:
            border, width, size = "#000000", 4, size + 4
        bright = not dim or n in focus
        sizes.append(size); borders.append(border); widths.append(width)
        opacity.append(1 if bright else 0.2)
        text_color.append("#2d3436" if bright else "#b2bec3")
        texts.append(f"<b>{step[n]}. {n}</b>" if n in step else n)
    fig.add_trace(go.Scatter(
        x=[pos[n][0] for n in all_nodes], y=[pos[n][1] for n in all_nodes],
        mode="markers+text", text=texts, textfont=dict(size=12, color=text_color),
        textposition=["top center" if n in children else "middle right" for n in all_nodes],
        customdata=all_nodes,                
        hovertext=[f"<b>{n}</b><br>{NODE[n]['type']} · {NODE[n]['subject']}" for n in all_nodes],
        hoverinfo="text", showlegend=False,
        marker=dict(size=sizes, opacity=opacity, line=dict(width=widths, color=borders),
                    color=[TYPE_STYLE[NODE[n]["type"]]["color"] for n in all_nodes],
                    symbol=[TYPE_STYLE[NODE[n]["type"]]["symbol"] for n in all_nodes])))

    if zoom and zoom.get("node") in pos:       
        y_range, height = zoom_range(zoom["node"]), ZOOM_ROWS * 32 + 110
    else:
        y_range, height = [TOTAL_ROWS - 0.5, -0.5], TOTAL_ROWS * 28 + 110
    fig.update_layout(
        height=int(height), plot_bgcolor="white", margin=dict(l=10, r=10, t=60, b=10),
        xaxis=dict(visible=False, range=[-0.7, 6]), yaxis=dict(visible=False, range=y_range),
        legend=dict(orientation="h", y=1.0, yanchor="bottom"),
        uirevision=f"v{(zoom or {}).get('token', 0)}")  
    return fig

# 4. Khung thông tin bên phải giao diện
MUTED = {"color": "#636e72", "fontSize": 13}
CARD = {"background": "white", "border": "1px solid #dfe6e9", "borderRadius": 8, "padding": 12, "marginBottom": 12}

def chip(text, color):
    return html.Span(text, style={"background": color, "color": "white", "fontSize": 11,
                                  "padding": "1px 7px", "borderRadius": 10, "marginRight": 6})

def link(r):
    """Link ngoài và/hoặc file tải lên của 1 tài nguyên, vị trí cần xem"""
    out = []
    if r["url"]:
        out.append(html.A(r["title"], href=r["url"], target="_blank", rel="noopener noreferrer"))
    if r["file_path"]:
        out += (["  ·  "] if out else []) + [html.A(f"📎 {r['file_name']}" if r["url"] else f"📎 {r['title']}",
                                                  href=f"/files/{r['file_path']}", target="_blank")]
    if r["note"]:
        out.append(html.Div(f"📍 {r['note']}", style=MUTED))
    return out

def render_info(selected):
    """Khung thông tin node"""
    if not selected:
        return html.Div([html.H3("Chưa chọn node", style={"margin": "0 0 6px"}),
                         html.Div(f"{len(all_nodes)} node · {len(EDGES)} quan hệ · {db.count_resources()} tài nguyên",
                                  style=MUTED),
                         html.P("Chọn kiến thức ở ô phía trên hoặc click 1 node. Click lại node đó để bỏ chọn.")])
    info, rels = NODE[selected], relations_of(selected)
    res = db.resources_of_node(selected)
    hint = ("Chưa có tài nguyên. Thêm ở trang «Quản lý tài nguyên»." if db.can_manage_resources(auth.current_user())
            else "Chưa có tài nguyên cho kiến thức này.")
    return html.Div([
        html.Div([chip(info["type"], TYPE_STYLE[info["type"]]["color"]), chip(info["subject"], "#636e72")]),
        html.H3(selected, style={"margin": "6px 0 2px"}),
        html.H4(f"Quan hệ ({len(rels)})", style={"margin": "12px 0 4px"}),
        html.Ul([html.Li(f"{text}: {other}", style={"color": REL_TEXT[rel]["color"]}) for other, rel, text in rels],
                style={"margin": 0, "paddingLeft": 20}),
        html.H4(f"Tài nguyên học tập ({len(res)})", style={"margin": "12px 0 4px"}),
        *([html.Div([chip(r["type"], "#636e72"), *link(r)], style={"margin": "6px 0"}) for r in res]
          or [html.Div(hint, style=MUTED)]),
    ])

def render_path(path):
    """Khung lộ trình học"""
    if not path:
        return html.Div([html.H4("Lộ trình học", style={"margin": "0 0 6px", "color": PATH_COLOR}),
                         html.Div("Chọn một kiến thức để xem cần học gì trước nó, theo thứ tự nào.", style=MUTED)])
    order, layers, why, target = path["order"], path["layers"], path["why"], path["target"]
    res = db.resources_of_nodes(order)
    by_stage = {}
    for i, n in enumerate(order, 1):
        by_stage.setdefault(layers[n], []).append((i, n))
    blocks = []
    for stage in sorted(by_stage):
        items = by_stage[stage]
        title = f"Giai đoạn {stage + 1}" + ("  ·  các mục này học song song được" if len(items) > 1 else "")
        rows = [html.Div([
            html.Div([html.Span(str(i), style={"background": PATH_COLOR, "color": "white", "borderRadius": 10,
                                               "padding": "0 7px", "marginRight": 8, "fontSize": 12}),
                      html.B(n), "  ", chip(NODE[n]["type"], TYPE_STYLE[NODE[n]["type"]]["color"]),
                      chip("ĐÍCH", "#d63031") if n == target else None]),
            html.Div(why[n], style={**MUTED, "marginLeft": 28}) if n in why else None,
            *[html.Div(link({**r, "note": r["note"]}), style={"marginLeft": 28, "fontSize": 13}) for r in res[n]],
        ], style={"padding": "6px 8px", "margin": "4px 0", "borderLeft": f"4px solid {PATH_COLOR}",
                  "background": "#e8e4ff" if n == target else "#f5f6fa"}) for i, n in items]
        blocks.append(html.Div([html.Div(title, style={"fontWeight": 700, "fontSize": 13, "marginTop": 10}), *rows]))
    return html.Div([html.H4(f"Lộ trình học để nắm «{target}»", style={"margin": "0 0 6px", "color": PATH_COLOR}),
                     html.Div(path["message"], style={"fontWeight": 600}), *blocks])

# 5. Layout
node_opts = [{"label": n, "value": n} for n in all_nodes]
type_opts, subject_opts = db.list_resource_types(), db.list_subjects()
LABEL = {"fontWeight": 600, "fontSize": 13, "display": "block", "marginBottom": 3}
FIELD = {"width": "100%", "boxSizing": "border-box", "padding": 6}
BTN = {"padding": "7px 12px", "cursor": "pointer"}
HIDDEN = {"display": "none"}
ZOOM_LABEL, BACK_LABEL = "🔍 Zoom tới node đã chọn", "↺ Xem toàn bộ"
ROLE_COLOR = {"admin": "#d63031", "user": "#636e72"}
PAGES = {"graph": " 🕸 Đồ thị tri thức", "res": " 📚 Quản lý tài nguyên",
         "admin": " 🛠 Quản lý người dùng", "account": " 👤 Tài khoản"}

def pages_for(user):
    """Người dùng chỉ có Đồ thị + Tài khoản, admin thấy cả 4 trang"""
    keys = list(PAGES) if user and user["role"] == "admin" else ["graph", "account"]
    return [{"label": PAGES[k], "value": k} for k in keys]

def field(label, component):
    return html.Div([html.Label(label, style=LABEL), component], style={"marginBottom": 10})

def ok(text):
    return html.Div(text, style={"color": "#00875a"})

def err(text):
    return html.Div(text, style={"color": "#d63031"})

server = Flask(__name__)                        
auth.init_auth(server) # gắn trang đăng nhập
app = Dash(__name__, server=server)
app.title = "Đồ thị tri thức"
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

page_res = html.Div(id="page-res", style=HIDDEN, children=[
    html.Div(style={"display": "flex", "gap": 12, "alignItems": "flex-start", "flexWrap": "wrap"}, children=[
        html.Div(style={**CARD, "flex": 1, "minWidth": 330}, children=[ # form thêm tài nguyên
            html.H3("Thêm tài nguyên", style={"marginTop": 0}),
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
            ])),
            field("Node kiến thức liên quan * (chọn nhiều được)",
                  dcc.Dropdown(id="f-nodes", options=node_opts, multi=True, value=[])),
            field("Vị trí cần xem (áp dụng cho các node đã chọn)",
                  dcc.Input(id="f-note", type="text", value="", placeholder="Ví dụ: Chương 3, trang 45-60", style=FIELD)),
            html.Button("➕ Thêm tài nguyên", id="add-btn", n_clicks=0, style=BTN),
            html.Div(id="res-msg", style={"marginTop": 8}),
        ]),
        html.Div(style={**CARD, "flex": 2, "minWidth": 480}, children=[ # danh sách tài nguyên
            html.H3("Danh sách tài nguyên", style={"marginTop": 0}),
            html.Div(style={"display": "flex", "gap": 10, "flexWrap": "wrap"}, children=[
                dcc.Input(id="res-search", type="text", value="", style={**FIELD, "width": 260},
                          placeholder="Tìm theo tiêu đề, mô tả, node (không cần dấu)"),
                dcc.Dropdown(id="res-filter-type", options=type_opts, placeholder="Mọi loại", style={"width": 170}),
                dcc.Dropdown(id="res-filter-subject", options=subject_opts, placeholder="Mọi môn", style={"width": 170}),
            ]),
            html.Div(id="res-table", style={"overflowX": "auto", "marginTop": 10}),
            html.Hr(),
            html.Div(style={"display": "flex", "gap": 10, "alignItems": "center", "flexWrap": "wrap"}, children=[
                dcc.Dropdown(id="res-pick", placeholder="Chọn tài nguyên cần xóa", style={"width": 360}),
                dcc.ConfirmDialogProvider(id="del-confirm", message="Bạn chắc chắn muốn xóa tài nguyên này?",
                                          children=html.Button("🗑 Xóa", style=BTN)),
            ]),
        ]),
    ]),
])

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
        ]),
    ]),
])

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
                      placeholder="Mật khẩu mới cho người đã chọn"),
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
    dcc.Store(id="sel"), # tên node đang chọn
    dcc.Store(id="zoom"), # "node": tên node hoặc None
    dcc.Store(id="res-version", data=0), # tăng 1 khi thêm/xóa tài nguyên
    dcc.Store(id="account-version", data=0), # tăng 1 khi đổi hồ sơ 
    dcc.Store(id="users-version", data=0), # tăng 1 khi admin đổi dữ liệu người dùng
])

# 6. CALLBACK: nối giao diện với code Python
NU = no_update
NOT_ADMIN = "Chỉ quản trị viên mới dùng được chức năng này."

@app.callback(Output("page-graph", "style"), Output("page-res", "style"),
              Output("page-account", "style"), Output("page-admin", "style"), Input("page", "value"))
def switch_page(page):
    return tuple({"display": "block"} if page == p else HIDDEN for p in ("graph", "res", "account", "admin"))

@app.callback(
    Output("user-box", "children"), Output("page", "options"),
    Output("acc-info", "children"), Output("acc-fullname", "value"), Output("acc-email", "value"),
    Input("url", "pathname"), Input("account-version", "data"))
def render_header(_path, _version):
    """Góc phải trên, danh sách trang theo vai trò, và thông tin ở trang Tài khoản"""
    user = auth.current_user()
    if not user:
        return html.A("Đăng nhập", href="/login"), pages_for(None), "", "", ""
    box = html.Div([html.Span(f"👤 {user['full_name'] or user['username']}", style={"fontWeight": 600, "marginRight": 8}),
                    chip(db.ROLE_LABEL[user["role"]], ROLE_COLOR[user["role"]]),
                    html.A("Đăng xuất", href="/logout", style={"marginLeft": 8})])
    info = html.Div([html.Div([html.B("Tên đăng nhập: "), user["username"]]),
                     html.Div([html.B("Vai trò: "), db.ROLE_LABEL[user["role"]]]),
                     html.Div([html.B("Tham gia: "), user["created_at"]]),
                     html.Div([html.B("Đăng nhập lần trước: "), user["last_login"] or "—"])])
    return box, pages_for(user), info, user["full_name"], user["email"]

# Chọn node: click đồ thị hoặc ô chọn kiến thức hoặc bấm nút zoom
@app.callback(
    Output("sel", "data"), Output("zoom", "data"), Output("zoom-btn", "children"), Output("graph", "clickData"),
    Input("graph", "clickData"), Input("node-pick", "value"), Input("zoom-btn", "n_clicks"),
    State("sel", "data"), State("zoom", "data"), prevent_initial_call=True)
def navigate(click_data, picked, _zoom_clicks, sel, zoom):
    trig = ctx.triggered_id
    token = (zoom or {}).get("token", 0) + 1    
    new_sel, new_zoom = sel, zoom
    if trig == "graph":
        node = ((click_data or {}).get("points") or [{}])[0].get("customdata")
        if node in NODE and node == sel: # click lại đúng node đang chọn -> BỎ CHỌN
            new_sel = None
            if zoom and zoom.get("node"): # đang zoom thì quay về
                new_zoom = {"node": None, "token": token}
        elif node in NODE:
            new_sel = node
    elif trig == "node-pick" and picked in NODE:
        new_sel = picked
    elif trig == "zoom-btn" and sel in NODE: # bấm lần nữa khi đang zoom node này -> xem toàn bộ
        new_zoom = {"node": None if zoomed_on(zoom, sel) else sel, "token": token}

    label = BACK_LABEL if zoomed_on(new_zoom, new_sel) else ZOOM_LABEL
    return (NU if new_sel == sel else new_sel), (NU if new_zoom is zoom else new_zoom), label, None

@app.callback(Output("graph", "figure"), Input("sel", "data"), Input("zoom", "data"))
def render_graph(sel, zoom):
    sel = sel if sel in NODE else None
    return make_figure(sel, zoom, get_path(sel))

@app.callback(Output("path-panel", "children"), Output("info-panel", "children"),
              Input("sel", "data"), Input("res-version", "data"))
def render_side(sel, _version):
    sel = sel if sel in NODE else None
    return render_path(get_path(sel)), render_info(sel)

# Quản lý tài nguyên (admin) 
OUT = ["msg", "version", "title", "desc", "type", "subject", "url", "nodes", "note", "pick", "up_contents", "up_filename"]
CLEAR = dict(title="", desc="", type=None, subject=None, url="", nodes=[], note="", up_contents=None, up_filename=None)

def result(**kw):
    """Tuple trả về theo đúng thứ tự Output của manage_resources, Output nào không nhắc tới thì giữ nguyên"""
    return tuple(kw.get(k, NU) for k in OUT)

@app.callback(Output("upload-label", "children"), Input("f-upload", "filename"))
def show_upload_name(filename):
    return f"📎 Đã chọn: {filename}" if filename else ""

@app.callback(
    Output("res-msg", "children"), Output("res-version", "data"),
    Output("f-title", "value"), Output("f-desc", "value"), Output("f-type", "value"),
    Output("f-subject", "value"), Output("f-url", "value"), Output("f-nodes", "value"),
    Output("f-note", "value"), Output("res-pick", "value"),
    Output("f-upload", "contents"), Output("f-upload", "filename"),
    Input("add-btn", "n_clicks"), Input("del-confirm", "submit_n_clicks"),
    State("f-title", "value"), State("f-desc", "value"), State("f-type", "value"), State("f-subject", "value"),
    State("f-url", "value"), State("f-nodes", "value"), State("f-note", "value"),
    State("f-upload", "contents"), State("f-upload", "filename"), State("res-pick", "value"),
    State("res-version", "data"), prevent_initial_call=True)
def manage_resources(_add, _del, title, desc, rtype, subject, url, nodes, note, up_contents, up_filename, pick, version):
    user = auth.current_user()
    if not db.can_manage_resources(user): 
        return result(msg=err(db.RESOURCE_ADMIN_ONLY))
    if ctx.triggered_id == "add-btn":
        saved = None
        try:
            if up_contents:
                saved = storage.save_upload(up_contents, up_filename) # (tên file đã lưu, tên gốc)
            rid = db.add_resource(title, desc, rtype, subject, url, nodes or [], note, user,
                                  file_path=saved[0] if saved else "", file_name=saved[1] if saved else "")
        except (ValueError, PermissionError) as e:
            if saved:
                storage.delete_file(saved[0]) # DB từ chối thì file vừa lưu cũng bỏ đi
            return result(msg=err(str(e)))
        return result(msg=ok(f"Đã thêm tài nguyên #{rid}."), version=(version or 0) + 1, **CLEAR)

    if pick is None: # nút Xóa
        return result(msg=err("Hãy chọn tài nguyên cần xóa."))
    try:
        storage.delete_file(db.delete_resource(pick, user)) # xóa luôn file đính kèm (nếu có)
    except (ValueError, PermissionError) as e:
        return result(msg=err(str(e)))
    return result(msg=ok(f"Đã xóa tài nguyên #{pick}."), version=(version or 0) + 1, pick=None)

@app.callback(
    Output("res-table", "children"), Output("res-pick", "options"),
    Input("res-search", "value"), Input("res-filter-type", "value"), Input("res-filter-subject", "value"),
    Input("res-version", "data"))
def render_res_table(keyword, type_name, subject_name, _version):
    if not db.can_manage_resources(auth.current_user()):
        return html.Div(NOT_ADMIN, style=MUTED), []
    rows = db.search_resources(keyword or "", type_name, subject_name)
    th = {"textAlign": "left", "padding": "6px 8px", "borderBottom": "2px solid #dfe6e9", "fontSize": 13}
    td = {"padding": "6px 8px", "borderBottom": "1px solid #f1f2f6", "fontSize": 14, "verticalAlign": "top"}
    table = html.Table(style={"borderCollapse": "collapse", "width": "100%"}, children=[
        html.Thead(html.Tr([html.Th(h, style=th) for h in
                            ["ID", "Tiêu đề (bấm để mở)", "Loại", "Môn", "Node liên quan (vị trí cần xem)", "Người thêm", "Ngày thêm"]])),
        html.Tbody([html.Tr([html.Td(r["id"], style=td), html.Td(link({**r, "note": ""}), style=td),
                             html.Td(r["type"], style=td), html.Td(r["subject"], style=td), html.Td(r["nodes"], style=td),
                             html.Td(r["owner"] or "(không rõ)", style=td), html.Td(r["created_at"][:10], style=td)])
                    for r in rows]),
    ])
    options = [{"label": f"#{r['id']} - {r['title']}", "value": r["id"]} for r in db.search_resources()]
    return html.Div([html.Div(f"{len(rows)} tài nguyên", style=MUTED),
                     table if rows else html.Div("Không có tài nguyên nào phù hợp.", style=MUTED)]), options

# Trang Tài khoản
@app.callback(Output("profile-msg", "children"), Output("account-version", "data"),
              Input("profile-btn", "n_clicks"), State("acc-fullname", "value"), State("acc-email", "value"),
              State("account-version", "data"), prevent_initial_call=True)
def save_profile(_n, fullname, email, version):
    user = auth.current_user()
    if not user:
        return err("Phiên đăng nhập đã hết hạn."), NU
    db.update_profile(user["id"], fullname, email)
    return ok("Đã lưu thông tin."), (version or 0) + 1

@app.callback(Output("pw-msg", "children"), Output("url", "href"),
              Input("pw-btn", "n_clicks"), State("pw-old", "value"), State("pw-new", "value"),
              State("pw-new2", "value"), prevent_initial_call=True)
def change_my_password(_n, old, new, new2):
    user = auth.current_user()
    if not user:
        return err("Phiên đăng nhập đã hết hạn"), NU
    if new != new2:
        return err("Hai mật khẩu mới không khớp"), NU
    try:
        db.change_password(user["id"], old, new)
    except ValueError as e:
        return err(str(e)), NU
    return ok("Đã đổi mật khẩu. Đang chuyển về trang đăng nhập..."), "/logout"

# Quản lý người dùng (admin)
@app.callback(Output("users-table", "children"), Output("user-pick", "options"),
              Input("users-version", "data"), Input("url", "pathname"))
def render_users(_version, _path):
    user = auth.current_user()
    if not user or user["role"] != "admin":
        return html.Div(NOT_ADMIN, style=MUTED), []
    users = db.list_users()
    th = {"textAlign": "left", "padding": "6px 8px", "borderBottom": "2px solid #dfe6e9", "fontSize": 13}
    td = {"padding": "6px 8px", "borderBottom": "1px solid #f1f2f6", "fontSize": 14}
    table = html.Table(style={"borderCollapse": "collapse", "width": "100%"}, children=[
        html.Thead(html.Tr([html.Th(h, style=th) for h in
                            ["ID", "Tên đăng nhập", "Họ tên", "Email", "Vai trò", "Trạng thái", "Tài nguyên", "Đăng nhập cuối"]])),
        html.Tbody([html.Tr([
            html.Td(u["id"], style=td), html.Td(u["username"], style=td), html.Td(u["full_name"], style=td),
            html.Td(u["email"], style=td), html.Td(chip(db.ROLE_LABEL[u["role"]], ROLE_COLOR[u["role"]]), style=td),
            html.Td("Hoạt động" if u["is_active"] else "🔒 Bị khóa", style=td),
            html.Td(u["n_resources"], style=td), html.Td(u["last_login"] or "—", style=td)]) for u in users]),
    ])
    options = [{"label": f"#{u['id']} - {u['username']} ({db.ROLE_LABEL[u['role']]})", "value": u["id"]} for u in users]
    return html.Div([html.Div(f"{len(users)} người dùng", style=MUTED), table]), options

@app.callback(Output("users-msg", "children"), Output("users-version", "data"),
              Output("newpw", "value"), Output("user-pick", "value"),
              Input("role-btn", "n_clicks"), Input("active-btn", "n_clicks"),
              Input("resetpw-btn", "n_clicks"), Input("user-del-confirm", "submit_n_clicks"),
              State("user-pick", "value"), State("role-pick", "value"), State("newpw", "value"),
              State("users-version", "data"), prevent_initial_call=True)
def manage_users(_r, _a, _p, _d, pick, role, newpw, version):
    admin = auth.current_user()
    if not admin or admin["role"] != "admin":
        return err(NOT_ADMIN), NU, NU, NU
    if pick is None:
        return err("Hãy chọn người dùng ở ô phía trên."), NU, NU, NU
    trig, target = ctx.triggered_id, db.get_user(pick)
    try:
        if trig == "role-btn":
            if role is None:
                return err("Hãy chọn vai trò mới."), NU, NU, NU
            db.set_user_role(pick, role, admin["id"])
            text = f"Đã đổi vai trò thành «{db.ROLE_LABEL[role]}»."
        elif trig == "active-btn":
            db.set_user_active(pick, not (target and target["is_active"]), admin["id"])
            text = "Đã khóa / mở khóa tài khoản."
        elif trig == "resetpw-btn":
            db.admin_set_password(pick, newpw, admin["id"])
            text = "Đã đặt lại mật khẩu."
        else:                                    # user-del-confirm
            db.delete_user(pick, admin["id"])
            return ok("Đã xóa tài khoản."), (version or 0) + 1, "", None
    except (ValueError, PermissionError) as e:
        return err(str(e)), NU, NU, NU
    return ok(text), (version or 0) + 1, "", NU

if __name__ == "__main__":
    app.run(debug=True)