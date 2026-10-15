def learning_order(target, prereqs, rank):
    """Danh sách mọi node cần học để tới `target` (target nằm cuối), đúng thứ tự học.
    DFS từ target đi ngược về các tiền đề; node nào xử lý XONG thì ghi vào danh sách
    (hậu thứ tự) => tiền đề luôn đứng trước node phụ thuộc vào nó = sắp xếp topo.
    `rank`: dict tên node -> số thứ tự gốc, để các node ngang hàng có thứ tự ổn định.
    Trả về (order, cycles); cycles là các quan hệ tạo vòng lặp (do dữ liệu nhập sai).
    """
    order, visited, in_stack, cycles = [], set(), set(), []

    def visit(n):
        if n in visited:
            return
        in_stack.add(n)
        for p in sorted(prereqs.get(n, ()), key=lambda x: rank.get(x, 0)):
            if p in in_stack:               # đang đi vòng tròn: A cần B, B lại cần A
                cycles.append((p, n))
                continue
            visit(p)
        in_stack.discard(n)
        visited.add(n)
        order.append(n)

    visit(target)
    return order, cycles

def stages(order, prereqs):
    """Chia thành 'giai đoạn': node cùng giai đoạn không phụ thuộc nhau (học song song được).
    layer = 0 nếu không có tiền đề; ngược lại 1 + layer lớn nhất của các tiền đề."""
    layer = {}
    for n in order:
        ps = [p for p in prereqs.get(n, ()) if p in layer]
        layer[n] = 0 if not ps else 1 + max(layer[p] for p in ps)
    return layer

class LearningGraph:
    """Giữ cây phân cấp + đồ thị phụ thuộc đã mở rộng, và tạo lộ trình học."""
    def __init__(self, edges, rank):
        self.rank = rank
        self.parent, self.children = {}, {}
        self.pre, self.uses = {}, {} # pre[B] = {A: A là tiền đề của B}; uses[A] = {B: A dùng B}
        for a, b, rel in edges:
            if rel == "part_of":
                if a not in self.parent: # mỗi node chỉ lấy 1 cha đầu tiên
                    self.parent[a] = b
                    self.children.setdefault(b, []).append(a)
            elif rel == "prerequisite":
                self.pre.setdefault(b, set()).add(a) # a học trước b
            elif rel == "uses":
                self.uses.setdefault(a, set()).add(b) # a dùng b => b học trước a
        self._desc = {}

        # eff[v] = mọi node phải học trước v (sau khi áp dụng quy tắc 1)
        self.eff = {}
        for v in rank:
            anc = self.ancestors(v)
            eff = set(self.pre.get(v, ())) | set(self.uses.get(v, ())) # quan hệ trực tiếp
            for a in anc:
                if a in self.parent: # bỏ qua node gốc (Môn học)
                    eff.update(self.pre.get(a, ())) # thừa hưởng tiền đề của nhóm cha
            eff.discard(v)
            eff.difference_update(anc) # không bắt học trước cả nhóm chứa mình
            self.eff[v] = eff

    # Cây phân cấp
    def ancestors(self, n):
        """Cha, ông, cụ... (gần nhất trước)."""
        result, seen = [], {n}
        while n in self.parent and self.parent[n] not in seen:
            n = self.parent[n]
            seen.add(n)
            result.append(n)
        return result

    def descendants(self, n):
        """Mọi node nằm dưới n (con, cháu...)."""
        if n not in self._desc:
            out = []
            for c in self.children.get(n, ()):
                out.append(c)
                out.extend(self.descendants(c))
            self._desc[n] = out
        return self._desc[n]

    # Kiểm tra chất lượng dữ liệu
    def warnings(self):
        """Các quan hệ đáng ngờ trong dữ liệu (để người nhập dữ liệu xem lại)."""
        out = []
        for after, befores in list(self.pre.items()) + list(self.uses.items()):
            for before in befores:
                if before in self.ancestors(after):
                    out.append(f"«{before}» là nhóm chứa «{after}» nhưng lại được ghi là tiền đề/được dùng "
                               f"bởi nó (quan hệ này bị bỏ qua khi tính lộ trình).")
        return out

    # Giải thích "vì sao phải học node này"
    def _why(self, n, order, eff):
        """Tìm node phía sau cần n, trả về (node đó, câu giải thích)."""
        for m in order[order.index(n) + 1:]:
            if n not in eff[m]:
                continue
            if n in self.pre.get(m, ()):
                return m, f"tiền đề của «{m}»"
            if n in self.uses.get(m, ()):
                return m, f"«{m}» sử dụng nó"
            if n in self.children.get(m, ()):
                return m, f"là thành phần của «{m}»"
            for a in self.ancestors(m):
                if n in self.pre.get(a, ()):
                    if a in order[order.index(n) + 1:]: # nhóm cha cũng nằm trong lộ trình
                        return a, f"tiền đề của «{a}»"
                    return m, f"tiền đề của «{a}» (nhóm chứa «{m}»)"
            return m, f"cần học trước «{m}»"
        return None, ""

    # Những thứ người học đã biết
    def _known_closure(self, known):
        """Biết node n  =>  đã biết mọi tiền đề của n, và biết cả nhóm n => biết các mục con."""
        done, stack = set(), list(known)
        while stack:
            n = stack.pop()
            if n in done:
                continue
            done.add(n)
            stack.extend(self.eff.get(n, ()))
            stack.extend(self.descendants(n))
        return done

    # hàm chính cho giao diện
    def make_path(self, target, known=()):
        """Lộ trình học để nắm `target`, bỏ qua những node trong `known`.
        Kết quả là dict thuần:
          order  : các node theo thứ tự học (target cuối cùng)
          layers : {node: giai đoạn (0, 1, 2...)}
          edges  : [[trước, sau], ...] để vẽ mũi tên
          why    : {node: lý do phải học}
          skipped: các node được bỏ qua vì đã biết"""
        known = [k for k in known if k in self.rank]
        done = self._known_closure(known)
        base = {"order": [target], "layers": {target: 0}, "edges": [], "why": {},
                "target": target, "skipped": []}
        if target in done:
            return {**base, "message": f"«{target}» nằm trong phần bạn đã biết "
                                       "(hoặc là tiền đề / thành phần của nội dung bạn đã biết)."}

        # các node nằm trong đích (đích + mọi con cháu) -> con học trước nhóm
        inside = set([target] + self.descendants(target))
        eff = dict(self.eff)
        for g in inside:
            if self.children.get(g):
                eff[g] = self.eff[g] | set(self.children[g])

        order, cycles = learning_order(target, eff, self.rank)
        skipped = [n for n in order if n in done]
        order = [n for n in order if n not in done]
        layers = stages(order, eff)
        # sắp theo giai đoạn (ổn định theo thứ tự DFS)
        pos = {n: i for i, n in enumerate(order)}
        order.sort(key=lambda n: (layers[n], pos[n]))
        in_path = set(order)

        # lý do + các cạnh nối (mỗi node trỏ tới node phía sau cần nó)
        why, edges = {}, []
        for n in order[:-1]:
            m, text = self._why(n, order, eff)
            why[n] = text
            if m:
                edges.append([n, m])
        for after in order: # thêm các quan hệ trực tiếp có trong dữ liệu
            direct = self.pre.get(after, set()) | self.uses.get(after, set())
            for before in sorted(direct, key=lambda x: self.rank.get(x, 0)):
                if before in self.ancestors(after): # nhóm chứa mình mà lại là tiền đề (dữ liệu mâu thuẫn): bỏ
                    continue
                if before in in_path and [before, after] not in edges:
                    edges.append([before, after])

        outside = len(in_path - inside)
        if len(inside) > 1:
            msg = (f"«{target}» là nhóm gồm {len(inside) - 1} mục con: lộ trình gồm {len(order)} mục "
                   f"({outside} kiến thức tiền đề bên ngoài + {len(in_path & inside)} mục trong nhóm).")
        elif outside == 0:
            msg = f"«{target}» không có kiến thức tiền đề (kể cả tiền đề của nhóm chứa nó), có thể học trực tiếp."
        else:
            msg = f"Cần học {outside} kiến thức trước khi học «{target}»."
        if skipped:
            msg += f" Đã bỏ qua {len(skipped)} mục bạn đã biết."
        if cycles:
            msg += f" Cảnh báo: dữ liệu có {len(cycles)} quan hệ tạo vòng lặp, đã bỏ qua."
        return {**base, "order": order, "layers": layers, "edges": edges, "why": why,
                "skipped": skipped, "message": msg}
