import math
from functools import lru_cache

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

import db
import learning

# 1. NẠP DỮ LIỆU TỪ DATABASE
db.init_db() # tạo knowledge.db từ csv
graph_nodes, EDGES = db.load_graph()          

NODE = {n["name"]: n for n in graph_nodes}      
all_nodes = [n["name"] for n in graph_nodes]
SUBJECTS = [n["name"] for n in graph_nodes if n["type"] == "Môn học"]
RANK = {name: i for i, name in enumerate(all_nodes)}  

LG = learning.LearningGraph(EDGES, RANK) # bộ tìm lộ trình học
for _w in LG.warnings():                       
    print("[Cảnh báo dữ liệu]", _w)

REL_TEXT = {
    "part_of":      {"fwd": "là 1 phần của",  "bwd": "bao gồm",           "color": "#B0BEC5"},
    "prerequisite": {"fwd": "là tiền đề của", "bwd": "cần học trước",     "color": "#E74C3C"},
    "uses":         {"fwd": "sử dụng",        "bwd": "được sử dụng bởi",  "color": "#F39C12"},
    "influences":   {"fwd": "ảnh hưởng đến",  "bwd": "chịu ảnh hưởng từ", "color": "#16A085"},
}

# KIỂU HIỂN THỊ NODE:
TYPE_STYLE = {
    "Môn học":        {"color": "#2D3436", "size": 26, "symbol": "diamond"},
    "Nhóm kiến thức": {"color": "#E17055", "size": 17, "symbol": "square"},
    "Chủ đề":         {"color": "#0984E3", "size": 12, "symbol": "circle"},
    "Khái niệm":      {"color": "#00B894", "size": 9,  "symbol": "circle"},
}
PALETTE = ["#2E86DE", "#10AC84", "#EE5A24", "#8E44AD", "#F39C12", "#2D3436"]
SUBJECT_COLOR = {s: PALETTE[i % len(PALETTE)] for i, s in enumerate(SUBJECTS)}
PATH_COLOR = "#6C5CE7" # màu làm nổi lộ trình học

# 2. XỬ LÝ ĐỒ THỊ: cây cha-con, vị trí, số quan hệ
parent, children = {}, {}
for a, b, rel in EDGES:
    if rel == "part_of" and a not in parent: # mỗi node chỉ lấy 1 cha đầu tiên
        parent[a] = b
        children.setdefault(b, []).append(a)

for n in graph_nodes: # node không có cha
    if n["name"] not in parent and n["name"] not in SUBJECTS:
        print(f"[Cảnh báo dữ liệu] «{n['name']}» chưa thuộc nhóm nào ('là 1 phần của'), "
              f"tạm gắn vào môn «{n['subject']}».")
        parent[n["name"]] = n["subject"]
        children.setdefault(n["subject"], []).append(n["name"])

degree = {n: 0 for n in all_nodes} # số quan hệ của mỗi node
for a, b, rel in EDGES:
    degree[a] += 1
    degree[b] += 1

# Vị trí: x = cấp bậc, y = số thứ tự dòng
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
    """List (node_kia, loại quan hệ, câu mô tả) của 1 node."""
    result = []
    for a, b, rel in EDGES:
        if a == node:
            result.append((b, rel, REL_TEXT[rel]["fwd"]))
        elif b == node:
            result.append((a, rel, REL_TEXT[rel]["bwd"]))
    return result

@lru_cache(maxsize=512)
def get_path(selected):
    """Lộ trình học tới node `selected` (None nếu chưa chọn node)."""
    return LG.make_path(selected) if selected in NODE else None
#zoom
def zoom_view(nodes):
    """Khung nhìn (y_range, số dòng) vừa đủ chứa các node trong `nodes`; None nếu xem toàn bộ."""
    ys = [pos[n][1] for n in (nodes or []) if n in pos]
    if not ys:
        return None
    lo, hi = min(ys), max(ys)
    rows = min(max(ZOOM_ROWS, hi - lo + 4), TOTAL_ROWS)
    start = max(-0.5, min((lo + hi) / 2 - rows / 2, TOTAL_ROWS - 0.5 - rows))
    return [start + rows, start], rows      

# 3. VẼ ĐỒ THỊ
def arrow_trace(pairs, color, width, arrow_size, opacity=1.0, name=None):
    """Vẽ nhiều cạnh có hướng A -> B
    Mỗi cạnh gồm 3 điểm [đầu A, GIỮA, đầu B] và 1 điểm None.
    Chỉ điểm GIỮA có marker hình mũi tên (size > 0); các điểm còn lại size = 0 (ẩn).
    angleref="previous": mũi tên tự xoay theo hướng từ điểm trước tới điểm này,
    tức là chỉ đúng chiều A -> B, bất kể trục x và y có tỉ lệ khác nhau."""
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

    # lộ trình học:
    path_order = (path or {}).get("order") or []
    has_path = len(path_order) > 1
    path_set = set(path_order) if has_path else set()
    related = set()
    if selected and not has_path: # không có lộ trình thì làm nổi các node liên quan
        related = {o for o, _, _ in relations_of(selected)} | {selected}
    focus = path_set | related | ({selected} if selected else set())
    dim = bool(focus) # có node nổi bật thì phần còn lại mờ đi

    # cạnh có hướng: 
    for rel in REL_TEXT:
        pairs = [(a, b) for a, b, r in EDGES if r == rel]
        fig.add_trace(arrow_trace(pairs, REL_TEXT[rel]["color"], 1, 8,
                                  opacity=0.12 if dim else 0.65, name=REL_TEXT[rel]["fwd"]))

    # chú thích cho màu node:
    for name, st in TYPE_STYLE.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=name,
                                 marker=dict(size=11, color=st["color"], symbol=st["symbol"])))

    # cạnh của node đang chọn:
    if selected and not has_path:
        for rel in REL_TEXT:
            pairs = [(a, b) for a, b, r in EDGES if r == rel and selected in (a, b)]
            if pairs:
                fig.add_trace(arrow_trace(pairs, REL_TEXT[rel]["color"], 3, 13))

    # cạnh của lộ trình học:
    if has_path:
        path_pairs = [(a, b) for a, b in path["edges"] if a in pos and b in pos]
        if path_pairs:
            fig.add_trace(arrow_trace(path_pairs, PATH_COLOR, 4, 14))

    # node: 
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
        customdata=all_nodes,                
        hovertext=hovers, hoverinfo="text", showlegend=False,
        marker=dict(size=sizes, color=fills, symbol=symbols, opacity=opac,
                    line=dict(width=bwidths, color=borders))))

    # khung nhìn: 
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

# 4. CÁC KHUNG THÔNG TIN BÊN PHẢI
MUTED = {"color": "#636e72", "fontSize": 13}
CARD = {"background": "white", "border": "1px solid #dfe6e9", "borderRadius": 8,
        "padding": 12, "marginBottom": 12}

def chip(text, color):
    return html.Span(text, style={"background": color, "color": "white", "fontSize": 11,
                                  "padding": "1px 7px", "borderRadius": 10, "marginRight": 6})

def link(r):
    """Link tài nguyên mở ở tab mới + vị trí cần xem."""
    return [html.A(r["title"], href=r["url"], target="_blank", rel="noopener noreferrer"),
            html.Div(f"📍 {r['note']}", style=MUTED) if r["note"] else None]

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
          if res else [html.Div("Chưa có tài nguyên. Thêm ở tab 'Quản lý tài nguyên'.", style=MUTED)]),
    ])

def render_path(path):
    """Khung 'Lộ trình học': các bước xếp theo giai đoạn, mỗi bước có lý do + tài nguyên."""
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

# 5. GIAO DIỆN 
node_opts = [{"label": n, "value": n} for n in all_nodes]
type_opts = db.list_resource_types()
subject_opts = db.list_subjects()
LABEL = {"fontWeight": 600, "fontSize": 13, "display": "block", "marginBottom": 3}
FIELD = {"width": "100%", "boxSizing": "border-box", "padding": 6}
BTN = {"padding": "7px 12px", "cursor": "pointer"}
ADD_LABEL, SAVE_LABEL = "➕ Thêm tài nguyên", "💾 Lưu thay đổi"

def field(label, component):
    return html.Div([html.Label(label, style=LABEL), component], style={"marginBottom": 10})

app = Dash(__name__)
app.title = "Đồ thị tri thức"

ZOOM_LABEL, BACK_LABEL = "🔍 Zoom tới node đã chọn", "↺ Xem toàn bộ"

page_graph = html.Div(id="page-graph", children=[
    html.Div("Chọn 1 kiến thức (hoặc click 1 node trên đồ thị) để xem lộ trình học. ",
             style={**MUTED, "marginBottom": 8}),
    html.Div(style={"display": "flex", "gap": 12, "flexWrap": "wrap", "alignItems": "flex-end"}, children=[
        field("Chọn kiến thức",
              dcc.Dropdown(id="node-pick", options=node_opts, placeholder="Gõ để tìm, ví dụ: SVM",
                           style={"width": 340})),
        html.Div(html.Button(ZOOM_LABEL, id="zoom-btn", n_clicks=0, style=BTN), style={"marginBottom": 10}),
    ]),
    # đồ thị, lộ trình và thông tin node
    html.Div(style={"display": "flex", "gap": 12, "alignItems": "flex-start"}, children=[
        html.Div(dcc.Graph(id="graph", config={"displaylogo": False}),
                 style={"flex": 3, "minWidth": 0, "height": "74vh", "overflowY": "auto",
                        "background": "white", "border": "1px solid #dfe6e9", "borderRadius": 8}),
        html.Div(style={"flex": 1, "minWidth": 340, "maxWidth": 480, "height": "74vh", "overflowY": "auto"},
                 children=[html.Div(id="path-panel", style=CARD), html.Div(id="info-panel", style=CARD)]),
    ]),
])

page_res = html.Div(id="page-res", style={"display": "none"}, children=[
    html.Div(style={"display": "flex", "gap": 12, "alignItems": "flex-start", "flexWrap": "wrap"}, children=[
        # form thêm / sửa tài nguyên 
        html.Div(style={**CARD, "flex": 1, "minWidth": 330}, children=[
            html.H3("Thêm / sửa tài nguyên", style={"marginTop": 0}),
            field("Tiêu đề *", dcc.Input(id="f-title", type="text", value="", style=FIELD)),
            field("Mô tả", dcc.Textarea(id="f-desc", value="", style={**FIELD, "height": 60})),
            field("Loại tài nguyên *", dcc.Dropdown(id="f-type", options=type_opts)),
            field("Môn học *", dcc.Dropdown(id="f-subject", options=subject_opts)),
            field("Đường dẫn (link nơi lưu trữ) *",
                  dcc.Input(id="f-url", type="text", value="", placeholder="https://...", style=FIELD)),
            field("Node kiến thức liên quan * (chọn nhiều được)",
                  dcc.Dropdown(id="f-nodes", options=node_opts, multi=True, value=[])),
            field("Vị trí cần xem (mỗi dòng 1 ghi chú)",
                  dcc.Textarea(id="f-note", value="", style={**FIELD, "height": 70},
                               placeholder="Chương 3, trang 45-60   ← áp dụng cho mọi node\n"
                                           "Tên node = Chương 5   ← riêng cho node đó")),
            html.Div([html.Button(ADD_LABEL, id="add-btn", n_clicks=0, style=BTN),
                      html.Button("Hủy sửa", id="cancel-btn", n_clicks=0, style={**BTN, "marginLeft": 6})]),
            html.Div(id="res-msg", style={"marginTop": 8}),
        ]),
        # danh sách + tìm kiếm + sửa / xóa
        html.Div(style={**CARD, "flex": 2, "minWidth": 480}, children=[
            html.H3("Danh sách tài nguyên", style={"marginTop": 0}),
            html.Div(style={"display": "flex", "gap": 10, "flexWrap": "wrap"}, children=[
                dcc.Input(id="res-search", type="text", value="", style={**FIELD, "width": 260},
                          placeholder="Tìm theo tiêu đề, mô tả, node, vị trí (không cần dấu)"),
                dcc.Dropdown(id="res-filter-type", options=type_opts, placeholder="Mọi loại", style={"width": 180}),
                dcc.Dropdown(id="res-filter-subject", options=subject_opts, placeholder="Mọi môn",
                             style={"width": 180}),
            ]),
            html.Div(id="res-table", style={"overflowX": "auto", "marginTop": 10}),
            html.Hr(),
            html.Div(style={"display": "flex", "gap": 10, "alignItems": "center", "flexWrap": "wrap"}, children=[
                dcc.Dropdown(id="res-pick", placeholder="Chọn tài nguyên để sửa / xóa", style={"width": 360}),
                html.Button("✏ Sửa", id="edit-btn", n_clicks=0, style=BTN),
                dcc.ConfirmDialogProvider(id="del-confirm", message="Bạn chắc chắn muốn xóa tài nguyên này?",
                                          children=html.Button("🗑 Xóa", style=BTN)),
            ]),
        ]),
    ]),
])

app.layout = html.Div(style={"fontFamily": "Arial", "padding": 12, "background": "#f5f6fa"}, children=[
    html.H2("Quản lý tài nguyên học tập bằng đồ thị tri thức", style={"margin": "0 0 6px"}),
    dcc.RadioItems(id="page", value="graph", inline=True, style={"marginBottom": 10},
                   options=[{"label": " 🕸 Đồ thị tri thức", "value": "graph"},
                            {"label": " 📚 Quản lý tài nguyên", "value": "res"}],
                   inputStyle={"marginLeft": 14, "marginRight": 4}),
    page_graph,
    page_res,
    # Các "kho nhớ" ở trình duyệt: lưu trạng thái dùng chung giữa các callback
    dcc.Store(id="sel"),                         # tên node đang chọn
    dcc.Store(id="zoom"),                        # {"nodes": [...], "token": số} - yêu cầu zoom
    dcc.Store(id="res-version", data=0),         # tăng 1 mỗi khi thêm/sửa/xóa tài nguyên -> vẽ lại các khung
    dcc.Store(id="editing-id"),                  # id tài nguyên đang sửa (None = đang thêm mới)
])

# 6. CALLBACK: nối giao diện với code Python
NU = no_update

@app.callback(Output("page-graph", "style"), Output("page-res", "style"), Input("page", "value"))
def switch_page(page):
    show, hide = {"display": "block"}, {"display": "none"}
    return (show, hide) if page == "graph" else (hide, show)

def zoomed_on(zoom, node):
    """Đồ thị đang zoom đúng vào `node` không?"""
    return bool(zoom) and zoom.get("nodes") == [node]

# Chọn node (click đồ thị hoặc ô chọn kiến thức) và nút zoom
@app.callback(
    Output("sel", "data"), Output("zoom", "data"), Output("zoom-btn", "children"),
    Input("graph", "clickData"), Input("node-pick", "value"), Input("zoom-btn", "n_clicks"),
    State("sel", "data"), State("zoom", "data"),
    prevent_initial_call=True)
def navigate(click_data, picked, _zoom_clicks, sel, zoom):
    trig = ctx.triggered_id                   
    new_sel, new_zoom = sel, zoom

    if trig == "graph":                         
        points = (click_data or {}).get("points") or [{}]
        node = points[0].get("customdata")
        if node in NODE: # click vào chỗ trống thì bỏ qua
            new_sel = node
    elif trig == "node-pick":           
        if picked in NODE:
            new_sel = picked
    elif trig == "zoom-btn":
        token = (zoom or {}).get("token", 0) + 1    
        if zoomed_on(zoom, sel):              
            new_zoom = {"nodes": [], "token": token}
        elif sel in NODE:
            new_zoom = {"nodes": [sel], "token": token}

    label = BACK_LABEL if zoomed_on(new_zoom, new_sel) else ZOOM_LABEL
    return (NU if new_sel == sel else new_sel), (NU if new_zoom is zoom else new_zoom), label

# Vẽ đồ thị: chạy lại mỗi khi node đang chọn hoặc khung nhìn thay đổi
@app.callback(Output("graph", "figure"), Input("sel", "data"), Input("zoom", "data"))
def render_graph(sel, zoom):
    sel = sel if sel in NODE else None
    return make_figure(sel, zoom, get_path(sel))

# Vẽ các khung thông tin bên phải (lộ trình + thông tin node)
@app.callback(
    Output("path-panel", "children"), Output("info-panel", "children"),
    Input("sel", "data"), Input("res-version", "data"))
def render_side(sel, _version):
    sel = sel if sel in NODE else None
    return render_path(get_path(sel)), render_info(sel)

# Thêm / sửa / xóa tài nguyên. Một callback xử lý cả 4 nút, phân biệt bằng ctx.triggered_id
OUT = ["msg", "version", "title", "desc", "type", "subject", "url", "nodes", "note", "pick", "editing", "btn"]

def result(**kw):
    """Tạo tuple trả về theo đúng thứ tự Output; Output nào không nhắc tới thì giữ nguyên."""
    return tuple(kw.get(k, NU) for k in OUT)

CLEAR = dict(title="", desc="", type=None, subject=None, url="", nodes=[], note="",
             editing=None, btn=ADD_LABEL)

@app.callback(
    Output("res-msg", "children"), Output("res-version", "data"),
    Output("f-title", "value"), Output("f-desc", "value"), Output("f-type", "value"),
    Output("f-subject", "value"), Output("f-url", "value"), Output("f-nodes", "value"),
    Output("f-note", "value"), Output("res-pick", "value"), Output("editing-id", "data"),
    Output("add-btn", "children"),
    Input("add-btn", "n_clicks"), Input("cancel-btn", "n_clicks"),
    Input("edit-btn", "n_clicks"), Input("del-confirm", "submit_n_clicks"),
    State("f-title", "value"), State("f-desc", "value"), State("f-type", "value"),
    State("f-subject", "value"), State("f-url", "value"), State("f-nodes", "value"),
    State("f-note", "value"), State("res-pick", "value"), State("editing-id", "data"),
    State("res-version", "data"),
    prevent_initial_call=True)
def manage_resources(_add, _cancel, _edit, _del, title, desc, rtype, subject, url, nodes, note,
                     pick, editing, version):
    version = version or 0
    ok = lambda text: html.Div(text, style={"color": "#00875a"})
    err = lambda text: html.Div(text, style={"color": "#d63031"})
    trig = ctx.triggered_id

    if trig == "add-btn": # thêm mới, hoặc lưu thay đổi nếu đang ở chế độ sửa
        links = db.parse_note_lines(note, nodes or [])
        try:
            if editing is not None:
                db.update_resource(editing, title, desc, rtype, subject, url, links)
                text = f"Đã cập nhật tài nguyên #{editing}."
            else:
                text = f"Đã thêm tài nguyên #{db.add_resource(title, desc, rtype, subject, url, links)}."
        except ValueError as e: # nhập thiếu / sai -> báo lỗi, giữ nguyên form
            return result(msg=err(str(e)))
        return result(msg=ok(text), version=version + 1, pick=None, **CLEAR)

    if trig == "cancel-btn":
        return result(msg=ok("Đã hủy sửa."), **CLEAR)

    if trig == "edit-btn": # nạp tài nguyên đã chọn lên form
        r = db.get_resource(pick) if pick is not None else None
        if r is None:
            return result(msg=err("Hãy chọn tài nguyên cần sửa."))
        return result(msg=ok(f"Đang sửa tài nguyên #{r['id']} - chỉnh xong bấm «Lưu thay đổi»."),
                      title=r["title"], desc=r["description"], type=r["type"], subject=r["subject"],
                      url=r["url"], nodes=list(r["links"]), note=db.format_notes(r["links"]),
                      editing=r["id"], btn=SAVE_LABEL)

    if trig == "del-confirm":
        if pick is None:
            return result(msg=err("Hãy chọn tài nguyên cần xóa."))
        db.delete_resource(pick)
        extra = CLEAR if editing == pick else {}  # đang sửa đúng tài nguyên vừa xóa -> thoát chế độ sửa
        return result(msg=ok(f"Đã xóa tài nguyên #{pick}."), version=version + 1, **{**extra, "pick": None})
    return result()

# Bảng danh sách tài nguyên + ô chọn tài nguyên để sửa / xóa
@app.callback(
    Output("res-table", "children"), Output("res-pick", "options"),
    Input("res-search", "value"), Input("res-filter-type", "value"),
    Input("res-filter-subject", "value"), Input("res-version", "data"))
def render_res_table(keyword, type_name, subject_name, _version):
    rows = db.search_resources(keyword or "", type_name, subject_name)
    th = {"textAlign": "left", "padding": "6px 8px", "borderBottom": "2px solid #dfe6e9", "fontSize": 13}
    td = {"padding": "6px 8px", "borderBottom": "1px solid #f1f2f6", "fontSize": 14, "verticalAlign": "top"}
    table = html.Table(style={"borderCollapse": "collapse", "width": "100%"}, children=[
        html.Thead(html.Tr([html.Th(h, style=th) for h in
                            ["ID", "Tiêu đề (bấm để mở)", "Loại", "Môn", "Node liên quan (vị trí cần xem)", "Ngày thêm"]])),
        html.Tbody([html.Tr([
            html.Td(r["id"], style=td),
            html.Td(html.A(r["title"], href=r["url"], target="_blank", rel="noopener noreferrer"), style=td),
            html.Td(r["type"], style=td), html.Td(r["subject"], style=td),
            html.Td(r["nodes"], style=td), html.Td(r["created_at"][:10], style=td),
        ]) for r in rows]),
    ])
    body = table if rows else html.Div("Không có tài nguyên nào phù hợp.", style=MUTED)
    options = [{"label": f"#{r['id']} - {r['title']}", "value": r["id"]} for r in db.search_resources("")]
    return html.Div([html.Div(f"{len(rows)} tài nguyên", style=MUTED), body]), options

if __name__ == "__main__":
    app.run(debug=True)
