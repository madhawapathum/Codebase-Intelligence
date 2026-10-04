import ast

from codebase_intelligence.visitor import ModuleVisitor


def test_visitor_extracts_classes_functions_and_calls():
    source = '''
class UserService:
    def get_user(self, user_id):
        return user_id

    def delete_user(self, user_id):
        return self.get_user(user_id)


def authenticate(user):
    return validate(user)
'''
    tree = ast.parse(source)
    visitor = ModuleVisitor("demo.py", "demo")
    visitor.visit(tree)

    assert any(item.name == "UserService" for item in visitor.classes)
    assert any(func.name == "authenticate" for func in visitor.functions)
    assert any(call["callee"] == "self.get_user" for call in visitor.calls)
