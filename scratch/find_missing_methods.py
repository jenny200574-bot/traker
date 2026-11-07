import ast

class MethodChecker(ast.NodeVisitor):
    def __init__(self):
        self.defined_methods = set()
        self.self_attributes = set()
        self.dialog_defined = set()
        self.dialog_attributes = set()
        self.current_class = None

    def visit_ClassDef(self, node):
        prev_class = self.current_class
        self.current_class = node.name
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                if node.name == 'DropiTrackerApp':
                    self.defined_methods.add(item.name)
                elif node.name == 'BodegaDialog':
                    self.dialog_defined.add(item.name)
        self.generic_visit(node)
        self.current_class = prev_class

    def visit_Attribute(self, node):
        # We look for self.attr_name
        if isinstance(node.value, ast.Name) and node.value.id == 'self':
            if self.current_class == 'DropiTrackerApp':
                self.self_attributes.add(node.attr)
            elif self.current_class == 'BodegaDialog':
                self.dialog_attributes.add(node.attr)
        self.generic_visit(node)

# Read app.py
with open("app.py", "r", encoding="utf-8") as f:
    tree = ast.parse(f.read())

checker = MethodChecker()
checker.visit(tree)

print("=== DROPI TRACKER APP METHODS ===")
print("Defined methods:")
for m in sorted(checker.defined_methods):
    print(f"  - {m}")

print("\nAccessed attributes/methods on self:")
missing_methods = []
for attr in sorted(checker.self_attributes):
    if attr.startswith('_') and attr not in checker.defined_methods:
        known_vars = {
            '_ui_queue', '_download_lock', '_running_download', '_scheduler', '_tabs',
            '_tab_config', '_tab_download', '_tab_dashboard', '_tab_historial',
            '_e_email', '_e_password', '_login_status', '_bodega_tree', '_hor_vars',
            '_btn_download', '_progress_bar', '_status_text', '_status_indicator',
            '_txt_log', '_stat_total', '_stat_bajos', '_stat_subieron', '_bodega_combo',
            '_hot_period', '_hot_tree', '_historial_tree', '_table_dashboard'
        }
        if attr not in known_vars:
            missing_methods.append(attr)

print("\nPossible missing methods in DropiTrackerApp:")
for m in sorted(missing_methods):
    print(f"  [X] {m}")

print("\n=== BODEGA DIALOG ===")
print("Defined methods:")
for m in sorted(checker.dialog_defined):
    print(f"  - {m}")
print("\nPossible missing in BodegaDialog:")
for attr in sorted(checker.dialog_attributes):
    if attr.startswith('_') and attr not in checker.dialog_defined:
        known_dialog_vars = {'result', 'master'}
        if attr not in known_dialog_vars:
            print(f"  [X] {attr}")
