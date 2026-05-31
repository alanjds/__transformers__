import ast
import io
import tokenize


def _replace_await_once(source):
    tokens_in = tokenize.generate_tokens(io.StringIO(source).readline)
    out = []
    for tok in tokens_in:
        if tok.type == tokenize.NAME and tok.string == 'await':
            out.append((tokenize.NAME, '__await_sync__'))
            out.append((tokenize.OP, '('))
            depth = 0
            for inner in tokens_in:
                if inner.type in (tokenize.NEWLINE, tokenize.ENDMARKER) and depth == 0:
                    out.append((tokenize.OP, ')'))
                    out.append((inner.type, inner.string))
                    break
                elif inner.type == tokenize.OP and inner.string in ('(', '[', '{'):
                    depth += 1
                    out.append((inner.type, inner.string))
                elif inner.type == tokenize.OP and inner.string in (')', ']', '}'):
                    depth -= 1
                    out.append((inner.type, inner.string))
                    if depth < 0:
                        out.append((tokenize.OP, ')'))
                        break
                    elif depth == 0:
                        out.append((tokenize.OP, ')'))
                        break
                else:
                    out.append((inner.type, inner.string))
        else:
            out.append((tok.type, tok.string))
    return tokenize.untokenize(out)


def source_preprocessor(source):
    """Replace 'await expr' with '__await_sync__(expr)' so sync functions parse cleanly."""
    prev = None
    while prev != source:
        prev = source
        source = _replace_await_once(source)
    return source


class AwaitSyncTransformer(ast.NodeTransformer):
    def __init__(self):
        self._in_async = False
        self._needs_asyncio = False

    def _is_await_sync_call(self, node):
        return (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Name) and
                node.func.id == '__await_sync__' and
                len(node.args) == 1 and
                not node.keywords)

    def visit_Module(self, node):
        self._in_async = False
        self._needs_asyncio = False
        self.generic_visit(node)
        if self._needs_asyncio:
            import_node = ast.Import(names=[ast.alias(name='asyncio', asname=None)])
            ast.fix_missing_locations(import_node)
            node.body.insert(0, import_node)
        return node

    def visit_AsyncFunctionDef(self, node):
        prev = self._in_async
        self._in_async = True
        self.generic_visit(node)
        self._in_async = prev
        return node

    def visit_FunctionDef(self, node):
        prev = self._in_async
        self._in_async = False
        self.generic_visit(node)
        self._in_async = prev
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if not self._is_await_sync_call(node):
            return node
        expr = node.args[0]
        if self._in_async:
            new_node = ast.Await(value=expr)
        else:
            self._needs_asyncio = True
            asyncio_run = ast.Attribute(
                value=ast.Name(id='asyncio', ctx=ast.Load()),
                attr='run',
                ctx=ast.Load(),
            )
            new_node = ast.Call(func=asyncio_run, args=[expr], keywords=[])
        return ast.fix_missing_locations(new_node)


transformer = AwaitSyncTransformer()
