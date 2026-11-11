"""Tìm lộ trình học (learning path) trên đồ thị tri thức"""

def learning_order(target, prereqs, rank):
    """Mọi node cần học để tới `target` (target ở cuối), tiền đề luôn đứng trước
    DFS đi ngược về các tiền đề"""
    order, visited, in_stack = [], set(), set()

    def visit(n):
        if n in visited:
            return
        in_stack.add(n)
        for p in sorted(prereqs.get(n, ()), key=lambda x: rank.get(x, 0)):
            if p not in in_stack: # gặp lại node đang xử lý = vòng lặp sai bỏ qua
                visit(p)
        in_stack.discard(n)
        visited.add(n)
        order.append(n)

    visit(target)
    return order

def stages(order, prereqs):
    """Giai đoạn của mỗi node: 0 nếu không có tiền đề, ngược lại 1 + giai đoạn lớn nhất của các tiền đề.
    Các node cùng giai đoạn không phụ thuộc nhau nên học song song được"""
    layer = {}
    for n in order:
        ps = [p for p in prereqs.get(n, ()) if p in layer]
        layer[n] = 1 + max(layer[p] for p in ps) if ps else 0
    return layer

class LearningGraph:
    def __init__(self, edges, rank):
        """edges: list (A, B, loại quan hệ); rank: {tên node: thứ tự gốc} để kết quả luôn ổn định"""
        self.rank = rank
        self.parent, self.children, self.pre, self.uses = {}, {}, {}, {}
        for a, b, rel in edges:
            if rel == "part_of" and a not in self.parent: # mỗi node chỉ lấy 1 cha đầu tiên
                self.parent[a] = b
                self.children.setdefault(b, []).append(a)
            elif rel == "prerequisite":
                self.pre.setdefault(b, set()).add(a) # a học trước b
            elif rel == "uses":
                self.uses.setdefault(a, set()).add(b) # a dùng b => b học trước a
        self.eff = {} # eff[v] = các node phải học trước v
        for v in rank:
            anc = self.ancestors(v)
            eff = set(self.pre.get(v, ())) | set(self.uses.get(v, ()))
            for a in anc:
                if a in self.parent: # bỏ node gốc (môn học)
                    eff |= self.pre.get(a, set())
            self.eff[v] = eff - {v} - set(anc)

    def ancestors(self, n):
        """Cha gần nhất trước"""
        result = []
        while n in self.parent and self.parent[n] not in result:
            n = self.parent[n]
            result.append(n)
        return result

    def descendants(self, n):
        """Con, cháu,... của n"""
        out = []
        for c in self.children.get(n, ()):
            out += [c] + self.descendants(c)
        return out

    def _why(self, n, order, eff):
        """Tìm node đứng sau n trong lộ trình mà cần n, kèm câu giải thích"""
        for m in order[order.index(n) + 1:]:
            if n not in eff[m]:
                continue
            if n in self.pre.get(m, ()):
                return m, f"tiền đề của «{m}»"
            if n in self.uses.get(m, ()):
                return m, f"«{m}» sử dụng nó"
            if n in self.children.get(m, ()):
                return m, f"là thành phần của «{m}»"
            for a in self.ancestors(m): # tiền đề thừa hưởng từ nhóm cha
                if n in self.pre.get(a, ()):
                    return (a, f"tiền đề của «{a}»") if a in order else (m, f"tiền đề của «{a}» (nhóm chứa «{m}»)")
        return None, ""

    def make_path(self, target):
        """Lộ trình học để nắm `target`. Kết quả là dict:
          order : các node theo thứ tự học (target cuối cùng)  layers : {node: giai đoạn}
          edges : [[trước, sau], ...] để vẽ mũi tên            why    : {node: lý do phải học}"""
        inside = {target, *self.descendants(target)}       
        eff = dict(self.eff)
        for g in inside:
            eff[g] = self.eff[g] | set(self.children.get(g, ()))
        order = learning_order(target, eff, self.rank)
        layers = stages(order, eff)
        pos = {n: i for i, n in enumerate(order)}
        order.sort(key=lambda n: (layers[n], pos[n]))       

        why, edges = {}, []
        for n in order[:-1]:
            m, why[n] = self._why(n, order, eff)
            if m:
                edges.append([n, m])

        outside = len(set(order) - inside)
        if len(inside) > 1:
            msg = (f"«{target}» là nhóm gồm {len(inside) - 1} mục con: lộ trình có {len(order)} mục "
                   f"({outside} kiến thức tiền đề bên ngoài + {len(order) - outside} mục trong nhóm).")
        elif outside == 0:
            msg = f"«{target}» không có kiến thức tiền đề, có thể học trực tiếp."
        else:
            msg = f"Cần học {outside} kiến thức trước khi học «{target}»."
        return {"order": order, "layers": layers, "edges": edges, "why": why, "target": target, "message": msg}