import ast
import asyncio
import concurrent.futures
import io
import tokenize


def _run_coro(coro):
    """Run a coroutine synchronously, regardless of whether an event loop is already running."""
    try:
        asyncio.get_running_loop()
        # Inside a running loop — execute in a fresh thread with its own loop
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


def _replace_await_once(source):
    tokens_in = tokenize.generate_tokens(io.StringIO(source).readline)
    out = []
    indent_level = 0
    func_stack = []     # (level_where_def_appeared, is_async)
    prev_was_async = False

    for tok in tokens_in:
        if tok.type == tokenize.INDENT:
            indent_level += 1
            out.append((tok.type, tok.string))
            prev_was_async = False

        elif tok.type == tokenize.DEDENT:
            indent_level -= 1
            while func_stack and func_stack[-1][0] >= indent_level:
                func_stack.pop()
            out.append((tok.type, tok.string))
            prev_was_async = False

        elif tok.type == tokenize.NAME and tok.string == 'async':
            prev_was_async = True
            out.append((tok.type, tok.string))

        elif tok.type == tokenize.NAME and tok.string == 'def':
            func_stack.append((indent_level, prev_was_async))
            out.append((tok.type, tok.string))
            prev_was_async = False

        elif tok.type == tokenize.NAME and tok.string == 'await':
            in_sync_def = func_stack and not func_stack[-1][1]
            if in_sync_def:
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
                        if depth < 0 or depth == 0:
                            out.append((tokenize.OP, ')'))
                            break
                    else:
                        out.append((inner.type, inner.string))
            else:
                out.append((tok.type, tok.string))
            prev_was_async = False

        else:
            out.append((tok.type, tok.string))
            prev_was_async = False

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
        self._needs_runner = False

    def _is_await_sync_call(self, node):
        return (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Name) and
                node.func.id == '__await_sync__' and
                len(node.args) == 1 and
                not node.keywords)

    def visit_Module(self, node):
        self._needs_runner = False
        self.generic_visit(node)
        if self._needs_runner:
            import_node = ast.ImportFrom(
                module='__transformers__.await_sync',
                names=[ast.alias(name='_run_coro', asname='__await_sync_run__')],
                level=0,
            )
            ast.fix_missing_locations(import_node)
            node.body.insert(0, import_node)
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if not self._is_await_sync_call(node):
            return node
        self._needs_runner = True
        return ast.fix_missing_locations(ast.Call(
            func=ast.Name(id='__await_sync_run__', ctx=ast.Load()),
            args=node.args,
            keywords=[],
        ))


transformer = AwaitSyncTransformer()
