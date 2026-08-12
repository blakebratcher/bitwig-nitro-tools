"""Nitro AST → human-readable pseudo-source emitter.

Phase E of the Nitro Decompiler v1 plan. Walks the AST produced by
`nitrobin_decompiler.decompile_nitrobin_file()` and emits Nitro-like
source code. Output is human-readable but not necessarily a recompilable
input, meant for DSP-intent analysis, not round-trip.

Children-layout knowledge for each AST kind comes from the packaged
`nitro_ast_class_methods.json`. This emitter hand-codes the layout per kind
because positional order alone isn't enough: we need to know which child is
"lhs" vs "rhs", "type" vs "name", etc.

Usage:
    from bitwig_nitro.nitro_pretty import pretty_print
    text = pretty_print(ast_root)
"""
from __future__ import annotations

from typing import Any

from .nitrobin_decompiler import AstNode

# Operator strings for binary/unary expression kinds
_BINARY_OPS = {
    "AddExpression": "+",
    "SubExpression": "-",
    "MulExpression": "*",
    "DivExpression": "/",
    "ModExpression": "%",
    "EqExpression": "==",
    "NEqExpression": "!=",
    "LTExpression": "<",
    "LEqExpression": "<=",
    "GTExpression": ">",
    "GEqExpression": ">=",
    "AndAndExpression": "&&",
    "OrOrExpression": "||",
    "AndExpression": "&",
    "OrExpression": "|",
    "XorExpression": "^",
    "LShiftExpression": "<<",
    "LRShiftExpression": ">>",
    "ARShiftExpression": ">>>",
    "AddReduceExpression": ".+",
    "MulReduceExpression": ".*",
    "AndAndReduceExpression": ".&&",
    "OrOrReduceExpression": ".||",
}

_UNARY_PREFIX = {
    "BangExpression": "!",
    "NotExpression": "~",
    "UnaryMinusExpression": "-",
    "AddressOfExpression": "&",
    "PointerDereferenceExpression": "*",
    "PreIncrementExpression": "++",
    "PreDecrementExpression": "--",
    "LikelyExpression": "likely",
    "IsFpClassExpression": "is_fpclass",
}

_UNARY_POSTFIX = {
    "PostIncrementExpression": "++",
    "PostDecrementExpression": "--",
}

# Singleton types (no children), emit by display name
_SINGLETON_TYPES = {
    "IntegerLiteralType": "int_lit",
    "FloatLiteralType": "float_lit",
    "Int8Type": "i8",
    "Int16Type": "i16",
    "Int32Type": "i32",
    "Int64Type": "i64",
    "UInt8Type": "u8",
    "UInt16Type": "u16",
    "UInt32Type": "u32",
    "UInt64Type": "u64",
    "Float32Type": "f32",
    "Float64Type": "f64",
    "BooleanType": "bool",
    "VoidType": "void",
    "AnyType": "any",
    "NullPointerType": "nullptr_t",
    "AnyIntegerType": "any_int",
    "AnyFloatType": "any_float",
}

# Specifiers (modifiers attached to declarations)
_SPECIFIERS = {
    "ConstSpecifier": "const",
    "ConstExprSpecifier": "constexpr",
    "StaticSpecifier": "static",
    "MonoSpecifier": "mono",
    "AlignAsSpecifier": "alignas",
}


class _Emitter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.indent = 0

    def _i(self) -> str:
        return "    " * self.indent

    def _line(self, s: str = "") -> None:
        if s:
            self.lines.append(self._i() + s)
        else:
            self.lines.append("")

    def _children_of_kind(self, n: AstNode, kind: str) -> list:
        """Return raw children list (filters out _decl_suffix dict)."""
        return [c for c in n.children if not (isinstance(c, dict) and "_decl_suffix" in c)]

    def _decl_suffix(self, n: AstNode) -> list[AstNode]:
        for c in n.children:
            if isinstance(c, dict) and "_decl_suffix" in c:
                return c["_decl_suffix"]
        return []

    # Public entry
    def emit(self, n: AstNode) -> str:
        self._emit_top(n)
        return "\n".join(self.lines).rstrip() + "\n"

    # ----- Top-level dispatch -----

    def _emit_top(self, n: AstNode) -> None:
        if n.kind == "NitroFile":
            decls = self._children_of_kind(n, "NitroFile")
            decl_list = decls[0] if decls else []
            for d in decl_list:
                self._emit_decl(d)
                self._line()
            return
        # Fallback: emit as expr
        self._line(self._expr(n))

    # ----- Declarations -----

    def _emit_decl(self, n: AstNode) -> None:
        if not hasattr(n, "kind"):
            self._line(f"// orphan: {n!r}")
            return
        method = getattr(self, f"_decl_{n.kind}", None)
        if method:
            method(n)
        else:
            # Generic declaration fallback
            self._line(f"// {n.kind}({len(n.children)} children), unimplemented")

    def _decl_ImportDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "ImportDeclaration")
        # read_sequence: super_init, read_string → 1 effective child (path)
        path = children[0] if children else "?"
        self._line(f'import "{path}";')

    def _decl_ComponentDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "ComponentDeclaration")
        name = children[0] if len(children) > 0 else "?"
        members = children[1] if len(children) > 1 else []
        self._line(f"struct {name}")
        self._line("{")
        self.indent += 1
        for m in members:
            self._emit_decl(m)
        self.indent -= 1
        self._line("}")
        # decl_suffix: nested decls
        suffix = self._decl_suffix(n)
        for d in suffix:
            self._emit_decl(d)

    def _decl_StructureDeclaration(self, n: AstNode) -> None:
        # Same shape as Component
        self._decl_ComponentDeclaration(n)

    def _decl_FunctionDeclaration(self, n: AstNode) -> None:
        # read: name, return_type, args_list, body, bool
        children = self._children_of_kind(n, "FunctionDeclaration")
        name = children[0] if len(children) > 0 else "?"
        ret_type = self._type(children[1]) if len(children) > 1 else "void"
        args = children[2] if len(children) > 2 else []
        body = children[3] if len(children) > 3 else None

        arg_strs = []
        for a in args:
            if hasattr(a, "kind") and a.kind == "FunctionArgumentDeclaration":
                ac = self._children_of_kind(a, "FunctionArgumentDeclaration")
                a_type = self._type(ac[0]) if ac else "?"
                a_name = ac[1] if len(ac) > 1 else "?"
                arg_strs.append(f"{a_type} {a_name}")
            else:
                arg_strs.append(self._expr(a))
        self._line(f"{ret_type} {name}({', '.join(arg_strs)})")
        if body is not None and hasattr(body, "kind") and body.kind == "BlockStatement":
            self._emit_block(body)
        else:
            self._line("{ }")

    def _decl_VariableDeclaration(self, n: AstNode) -> None:
        # read: list_specifiers, type, name, init_optional
        children = self._children_of_kind(n, "VariableDeclaration")
        specs = children[0] if children and isinstance(children[0], list) else []
        type_node = children[1] if len(children) > 1 else None
        name = children[2] if len(children) > 2 else "?"
        init = children[3] if len(children) > 3 else None
        spec_str = " ".join(_SPECIFIERS.get(s.kind, s.kind) for s in specs if hasattr(s, "kind"))
        type_str = self._type(type_node) if type_node else "?"
        head = f"{spec_str + ' ' if spec_str else ''}{type_str} {name}"
        if init is not None:
            self._line(f"{head} = {self._expr(init)};")
        else:
            self._line(f"{head};")

    def _decl_PortDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "PortDeclaration")
        port_type = self._type(children[0]) if children else "?"
        name = children[1] if len(children) > 1 else "?"
        self._line(f"{port_type} {name};")

    def _decl_ConstantDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "ConstantDeclaration")
        # Common shape: [specifiers, type, name, value]
        # Adjust if the read_sequence differs
        if len(children) >= 4 and isinstance(children[0], list):
            specs = children[0]
            type_node = children[1]
            name = children[2]
            value = children[3]
        elif len(children) == 3:
            specs = []
            type_node = children[0]
            name = children[1]
            value = children[2]
        else:
            self._line(f"// ConstantDeclaration({len(children)}), unhandled shape")
            return
        spec_str = " ".join(
            _SPECIFIERS.get(s.kind, s.kind) for s in specs if hasattr(s, "kind")
        )
        type_str = self._type(type_node)
        self._line(f"{spec_str + ' ' if spec_str else ''}const {type_str} {name} = {self._expr(value)};")

    def _decl_ConstantStringDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "ConstantStringDeclaration")
        name = children[0] if children else "?"
        value = children[1] if len(children) > 1 else "?"
        self._line(f'const string {name} = "{value}";')

    def _decl_TypeAliasDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "TypeAliasDeclaration")
        name = children[0] if children else "?"
        type_node = children[1] if len(children) > 1 else None
        self._line(f"typealias {self._type(type_node)} {name};")

    def _decl_EnumDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "EnumDeclaration")
        name = children[0] if children else "?"
        type_node = children[1] if len(children) > 1 else None
        entries = children[2] if len(children) > 2 else []
        type_str = f": {self._type(type_node)}" if type_node else ""
        self._line(f"enum {name}{type_str}")
        self._line("{")
        self.indent += 1
        for e in entries:
            if hasattr(e, "kind") and e.kind == "EnumEntryDeclaration":
                ec = self._children_of_kind(e, "EnumEntryDeclaration")
                ename = ec[0] if ec else "?"
                eval_ = ec[1] if len(ec) > 1 else None
                if eval_ is not None:
                    self._line(f"{ename} = {self._expr(eval_)},")
                else:
                    self._line(f"{ename},")
        self.indent -= 1
        self._line("}")

    def _decl_PropertyDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "PropertyDeclaration")
        # Approximate shape, refine after empirical inspection
        self._line(f"// property: {self._show_summary(children)}")

    def _decl_StaticAssertDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "StaticAssertDeclaration")
        cond = self._expr(children[0]) if children else "?"
        msg = ""
        if len(children) > 1 and isinstance(children[1], str):
            msg = f', "{children[1]}"'
        self._line(f"static_assert({cond}{msg});")

    def _decl_ProcessBlockDeclaration(self, n: AstNode) -> None:
        self._line("process")
        children = self._children_of_kind(n, "ProcessBlockDeclaration")
        body = children[0] if children else None
        if body and hasattr(body, "kind") and body.kind == "BlockStatement":
            self._emit_block(body)
        else:
            self._line("{ }")

    def _decl_StartBlockDeclaration(self, n: AstNode) -> None:
        self._block_decl(n, "start")

    def _decl_InitBlockDeclaration(self, n: AstNode) -> None:
        self._block_decl(n, "init")

    def _decl_PreProcessBlockDeclaration(self, n: AstNode) -> None:
        self._block_decl(n, "pre_process")

    def _decl_PostProcessBlockDeclaration(self, n: AstNode) -> None:
        self._block_decl(n, "post_process")

    def _decl_ResetBlockDeclaration(self, n: AstNode) -> None:
        self._block_decl(n, "reset")

    def _decl_StopBlockDeclaration(self, n: AstNode) -> None:
        self._block_decl(n, "stop")

    def _decl_PostEventsBlockDeclaration(self, n: AstNode) -> None:
        self._block_decl(n, "post_events")

    def _decl_ShouldProcessBlockDeclaration(self, n: AstNode) -> None:
        self._block_decl(n, "should_process")

    def _block_decl(self, n: AstNode, keyword: str) -> None:
        self._line(keyword)
        children = self._children_of_kind(n, n.kind)
        body = children[0] if children else None
        if body and hasattr(body, "kind") and body.kind == "BlockStatement":
            self._emit_block(body)
        else:
            self._line("{ }")

    def _decl_FunctionArgumentDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "FunctionArgumentDeclaration")
        type_str = self._type(children[0]) if children else "?"
        name = children[1] if len(children) > 1 else "?"
        self._line(f"{type_str} {name};")

    def _decl_TemplateDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "TemplateDeclaration")
        # Shape: parameters list + inner declaration. Best-effort.
        self._line(f"// template: {self._show_summary(children)}")

    def _decl_MacroDeclaration(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "MacroDeclaration")
        self._line(f"// macro: {self._show_summary(children)}")

    # ----- Statements -----

    def _emit_stmt(self, n: AstNode) -> None:
        if not hasattr(n, "kind"):
            self._line(f"// orphan stmt: {n!r}")
            return
        method = getattr(self, f"_stmt_{n.kind}", None)
        if method:
            method(n)
            return
        # Treat as decl if it ends in Declaration
        if n.kind.endswith("Declaration"):
            self._emit_decl(n)
            return
        # Treat as expression statement
        self._line(f"{self._expr(n)};")

    def _emit_block(self, n: AstNode) -> None:
        # BlockStatement has 3 child lists per Phase B. Empirically the third
        # list contains the statement body; the other two are annotations or
        # locals. Emit all non-empty lists with separators for readability.
        children = self._children_of_kind(n, "BlockStatement")
        self._line("{")
        self.indent += 1
        for sub in children:
            if isinstance(sub, list):
                for item in sub:
                    self._emit_stmt(item) if hasattr(item, "kind") else None
        self.indent -= 1
        self._line("}")

    def _stmt_BlockStatement(self, n: AstNode) -> None:
        self._emit_block(n)

    def _stmt_AssignStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "AssignStatement")
        lhs = self._expr(children[0]) if children else "?"
        rhs = self._expr(children[1]) if len(children) > 1 else "?"
        self._line(f"{lhs} = {rhs};")

    def _stmt_IfStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "IfStatement")
        cond = self._expr(children[0]) if children else "?"
        then_branch = children[1] if len(children) > 1 else None
        else_branch = children[2] if len(children) > 2 else None
        self._line(f"if ({cond})")
        self._emit_branch(then_branch)
        if else_branch is not None:
            self._line("else")
            self._emit_branch(else_branch)

    def _emit_branch(self, n: AstNode | None) -> None:
        if n is None:
            self._line("{ }")
            return
        if hasattr(n, "kind") and n.kind == "BlockStatement":
            self._emit_block(n)
        else:
            self.indent += 1
            self._emit_stmt(n)
            self.indent -= 1

    def _stmt_ReturnStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "ReturnStatement")
        value = children[0] if children else None
        if value is None:
            self._line("return;")
        else:
            self._line(f"return {self._expr(value)};")

    def _stmt_BreakStatement(self, n: AstNode) -> None:
        self._line("break;")

    def _stmt_ContinueStatement(self, n: AstNode) -> None:
        self._line("continue;")

    def _stmt_ForStatement(self, n: AstNode) -> None:
        # 5 nullable slots; empirical order: [init, ?, cond, update, body]
        children = self._children_of_kind(n, "ForStatement")
        init = self._expr_or_blank(children[0] if children else None)
        cond = self._expr_or_blank(children[2] if len(children) > 2 else None)
        upd = self._expr_or_blank(children[3] if len(children) > 3 else None)
        body = children[4] if len(children) > 4 else None
        self._line(f"for ({init}; {cond}; {upd})")
        self._emit_branch(body)

    def _stmt_WhileStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "WhileStatement")
        cond = self._expr(children[0]) if children else "?"
        body = children[1] if len(children) > 1 else None
        self._line(f"while ({cond})")
        self._emit_branch(body)

    def _stmt_DoWhileStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "DoWhileStatement")
        body = children[0] if children else None
        cond = self._expr(children[1]) if len(children) > 1 else "?"
        self._line("do")
        self._emit_branch(body)
        self._line(f"while ({cond});")

    def _stmt_SwitchStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "SwitchStatement")
        cond = self._expr(children[0]) if children else "?"
        cases = children[1] if len(children) > 1 else []
        self._line(f"switch ({cond})")
        self._line("{")
        self.indent += 1
        for c in cases:
            if hasattr(c, "kind"):
                self._emit_stmt(c)
        self.indent -= 1
        self._line("}")

    def _stmt_CaseStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "CaseStatement")
        labels = children[0] if children and isinstance(children[0], list) else []
        body = children[1] if len(children) > 1 else None
        for lab in labels:
            self._line(f"case {self._expr(lab)}:")
        self._emit_branch(body)

    def _stmt_AssertStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "AssertStatement")
        cond = self._expr(children[0]) if children else "?"
        self._line(f"assert {cond};")

    def _stmt_BasicSendStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "BasicSendStatement")
        self._line(f"send {self._show_summary(children)};")

    def _stmt_SendStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "SendStatement")
        self._line(f"send {self._show_summary(children)};")

    def _stmt_SendEmplaceStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "SendEmplaceStatement")
        self._line(f"send_emplace {self._show_summary(children)};")

    def _stmt_TriggerSendStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "TriggerSendStatement")
        self._line(f"trigger_send {self._show_summary(children)};")

    def _stmt_PrintlnStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "PrintlnStatement")
        args = ", ".join(
            self._expr(c) for c in (children[0] if children and isinstance(children[0], list) else children)
        )
        self._line(f"println({args});")

    def _stmt_DefaultCaseStatement(self, n: AstNode) -> None:
        children = self._children_of_kind(n, "DefaultCaseStatement")
        body = children[0] if children else None
        self._line("default:")
        self._emit_branch(body)

    # ----- Expressions -----

    def _expr_or_blank(self, n: Any) -> str:
        if n is None:
            return ""
        return self._expr(n)

    def _expr(self, n: Any) -> str:
        if n is None:
            return "null"
        if isinstance(n, bool):
            return "true" if n else "false"
        if isinstance(n, str):
            return repr(n)  # quoted string literal
        if isinstance(n, int):
            return str(n)
        if isinstance(n, float):
            return repr(n)
        if not hasattr(n, "kind"):
            return f"<?{type(n).__name__}?>"
        kind = n.kind
        if kind in _BINARY_OPS:
            children = self._children_of_kind(n, kind)
            lhs = self._expr(children[0]) if children else "?"
            rhs = self._expr(children[1]) if len(children) > 1 else "?"
            return f"({lhs} {_BINARY_OPS[kind]} {rhs})"
        if kind in _UNARY_PREFIX:
            children = self._children_of_kind(n, kind)
            inner = self._expr(children[0]) if children else "?"
            return f"{_UNARY_PREFIX[kind]}{inner}"
        if kind in _UNARY_POSTFIX:
            children = self._children_of_kind(n, kind)
            inner = self._expr(children[0]) if children else "?"
            return f"{inner}{_UNARY_POSTFIX[kind]}"
        method = getattr(self, f"_expr_{kind}", None)
        if method:
            return method(n)
        # Fallback for unknown
        return f"<{kind}>"

    def _expr_IdentifierExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "IdentifierExpression")
        return str(children[0]) if children else "?"

    def _expr_IntegerValueExpression(self, n: AstNode) -> str:
        # children: [type, value]
        children = self._children_of_kind(n, "IntegerValueExpression")
        val = children[1] if len(children) > 1 else 0
        return str(val)

    def _expr_FloatValueExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "FloatValueExpression")
        val = children[1] if len(children) > 1 else 0.0
        return repr(val)

    def _expr_BooleanValueExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "BooleanValueExpression")
        return "true" if children and children[0] else "false"

    def _expr_StringValueExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "StringValueExpression")
        return repr(children[0]) if children else '""'

    def _expr_TypeValueExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "TypeValueExpression")
        return self._type(children[0]) if children else "?"

    def _expr_NullPointerExpression(self, n: AstNode) -> str:
        return "nullptr"

    def _expr_ZeroInitValueExpression(self, n: AstNode) -> str:
        return "zero_init"

    def _expr_MemberAccessExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "MemberAccessExpression")
        obj = self._expr(children[0]) if children else "?"
        member = children[1] if len(children) > 1 else "?"
        return f"{obj}.{member}"

    def _expr_CallExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "CallExpression")
        callee = self._expr(children[0]) if children else "?"
        args = children[1] if len(children) > 1 and isinstance(children[1], list) else []
        return f"{callee}({', '.join(self._expr(a) for a in args)})"

    def _expr_MacroCallExpression(self, n: AstNode) -> str:
        return self._expr_CallExpression(n)

    def _expr_ArrayAccessExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "ArrayAccessExpression")
        arr = self._expr(children[0]) if children else "?"
        idx = self._expr(children[1]) if len(children) > 1 else "?"
        return f"{arr}[{idx}]"

    def _expr_CastExpression(self, n: AstNode) -> str:
        # children: [expr, type, bool]
        children = self._children_of_kind(n, "CastExpression")
        value = self._expr(children[0]) if children else "?"
        type_str = self._type(children[1]) if len(children) > 1 else "?"
        return f"cast({value}, {type_str})"

    def _expr_BitCastExpression(self, n: AstNode) -> str:
        # children: [expr, type]
        children = self._children_of_kind(n, "BitCastExpression")
        value = self._expr(children[0]) if children else "?"
        type_str = self._type(children[1]) if len(children) > 1 else "?"
        return f"bitcast({value}, {type_str})"

    def _expr_SelectExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "SelectExpression")
        cond = self._expr(children[0]) if children else "?"
        t = self._expr(children[1]) if len(children) > 1 else "?"
        f = self._expr(children[2]) if len(children) > 2 else "?"
        return f"({cond} ? {t} : {f})"

    def _expr_ConditionalExpression(self, n: AstNode) -> str:
        return self._expr_SelectExpression(n)

    def _expr_TemplateInstanceExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "TemplateInstanceExpression")
        return f"<template_instance:{self._show_summary(children)}>"

    def _expr_ArrayValueExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "ArrayValueExpression")
        items = []
        for c in children:
            if isinstance(c, list):
                items.extend(self._expr(x) for x in c)
            else:
                items.append(self._expr(c))
        return f"[{', '.join(items)}]"

    def _expr_ArrayFillExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "ArrayFillExpression")
        if children:
            return f"[{self._expr(children[0])}...]"
        return "[...]"

    def _expr_VectorValueExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "VectorValueExpression")
        items = []
        for c in children:
            if isinstance(c, list):
                items.extend(self._expr(x) for x in c)
            else:
                items.append(self._expr(c))
        return f"<{', '.join(items)}>"

    def _expr_EnumValueExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "EnumValueExpression")
        type_str = self._type(children[0]) if children else "?"
        value = children[1] if len(children) > 1 else "?"
        return f"{type_str}.{value}"

    def _expr_SizeofTypeExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "SizeofTypeExpression")
        return f"sizeof({self._type(children[0])})" if children else "sizeof(?)"

    def _expr_ShuffleVectorExpression(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "ShuffleVectorExpression")
        parts = []
        for c in children:
            if isinstance(c, list):
                parts.extend(self._expr(x) for x in c)
            else:
                parts.append(self._expr(c))
        return f"shuffle_vector({', '.join(parts)})"

    def _expr_VariableDeclaration(self, n: AstNode) -> str:
        # Variable used as for-loop init. Same shape as decl; render inline.
        children = self._children_of_kind(n, "VariableDeclaration")
        type_node = children[1] if len(children) > 1 else None
        name = children[2] if len(children) > 2 else "?"
        init = children[3] if len(children) > 3 else None
        type_str = self._type(type_node) if type_node else "?"
        if init is not None:
            return f"{type_str} {name} = {self._expr(init)}"
        return f"{type_str} {name}"

    # ----- Types -----

    def _type(self, n: Any) -> str:
        if n is None:
            return "?"
        if isinstance(n, str):
            return n
        if not hasattr(n, "kind"):
            return f"<?{type(n).__name__}?>"
        if n.kind in _SINGLETON_TYPES:
            return _SINGLETON_TYPES[n.kind]
        method = getattr(self, f"_type_{n.kind}", None)
        if method:
            return method(n)
        return f"<{n.kind}>"

    def _type_NamedType(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "NamedType")
        return str(children[0]) if children else "?"

    def _type_VectorType(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "VectorType")
        elem = self._type(children[0]) if children else "?"
        length = children[1] if len(children) > 1 else None
        if length is None:
            return f"{elem}<>"
        return f"{elem}<{self._expr(length)}>"

    def _type_ArrayType(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "ArrayType")
        elem = self._type(children[0]) if children else "?"
        length = children[1] if len(children) > 1 else None
        if length is None:
            return f"{elem}[]"
        return f"{elem}[{self._expr(length)}]"

    def _type_InputAudioPortType(self, n: AstNode) -> str:
        return "audio_inport"

    def _type_OutputAudioPortType(self, n: AstNode) -> str:
        return "audio_outport"

    def _type_InputValuePortType(self, n: AstNode) -> str:
        return "value_inport"

    def _type_OutputValuePortType(self, n: AstNode) -> str:
        return "value_outport"

    def _type_InputEventPortType(self, n: AstNode) -> str:
        return "event_inport"

    def _type_OutputEventPortType(self, n: AstNode) -> str:
        return "event_outport"

    def _type_DisplayOutputType(self, n: AstNode) -> str:
        return "display_outport"

    def _type_PointerType(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "PointerType")
        return f"{self._type(children[0])}*" if children else "?*"

    def _type_ReferenceType(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "ReferenceType")
        return f"{self._type(children[0])}&" if children else "?&"

    def _type_ConstQualifiedType(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "ConstQualifiedType")
        return f"const {self._type(children[0])}" if children else "const ?"

    def _type_TemplateInstanceType(self, n: AstNode) -> str:
        children = self._children_of_kind(n, "TemplateInstanceType")
        if not children:
            return "<template>"
        base = self._type(children[0])
        args = []
        for c in children[1:]:
            if isinstance(c, list):
                args.extend(self._type(x) if hasattr(x, "kind") and x.kind.endswith("Type") else self._expr(x) for x in c)
            elif hasattr(c, "kind") and c.kind.endswith("Type"):
                args.append(self._type(c))
            else:
                args.append(self._expr(c))
        if args:
            return f"{base}!<{', '.join(args)}>"
        return base

    def _type_TypeSwitchType(self, n: AstNode) -> str:
        return f"<TypeSwitch>"

    def _type_TemplateInstanceExpression(self, n: AstNode) -> str:
        # TemplateInstance used as type slot (e.g. variable type: `Delay!<f32, 8> d;`)
        children = self._children_of_kind(n, "TemplateInstanceExpression")
        if not children:
            return "<template>"
        # Empirical layout: last entry is a list of template args; preceding
        # entries are the base type identifier and inner instance metadata.
        base = "?"
        args: list[str] = []
        for c in children:
            if isinstance(c, list):
                for x in c:
                    if hasattr(x, "kind") and x.kind.endswith("Type"):
                        args.append(self._type(x))
                    else:
                        args.append(self._expr(x))
            elif hasattr(c, "kind"):
                if c.kind == "IdentifierExpression":
                    ic = self._children_of_kind(c, "IdentifierExpression")
                    if ic:
                        base = str(ic[0])
                elif c.kind.endswith("Type"):
                    args.append(self._type(c))
                else:
                    args.append(self._expr(c))
        if args:
            return f"{base}!<{', '.join(args)}>"
        return base

    # ----- Helpers -----

    def _show_summary(self, children: list) -> str:
        parts = []
        for c in children[:4]:
            if hasattr(c, "kind"):
                parts.append(c.kind)
            elif isinstance(c, list):
                parts.append(f"[{len(c)}]")
            else:
                parts.append(repr(c)[:30])
        if len(children) > 4:
            parts.append("...")
        return ", ".join(parts)


def pretty_print(ast: AstNode) -> str:
    """Emit Nitro pseudo-source from a decompiled AST tree."""
    return _Emitter().emit(ast)
