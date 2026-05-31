import ast
import asyncio
import re

from __transformers__.await_sync import source_preprocessor, transformer


def _transform(code):
    source = source_preprocessor(code)
    tree = ast.parse(source)
    tree = transformer.visit(tree)
    return tree


def _exec(code):
    tree = _transform(code)
    bytecode = compile(tree, '<string>', 'exec')
    ns = {}
    exec(bytecode, ns)
    return ns


def _has_bare_await(source):
    """True if source contains the 'await' keyword (not as part of __await_sync__)."""
    return bool(re.search(r'\bawait\b', source))


class TestSourcePreprocessor:
    def test_replaces_await_in_sync_function(self):
        source = 'def f():\n    x = await coro()\n'
        result = source_preprocessor(source)
        assert not _has_bare_await(result)
        assert '__await_sync__' in result
        # Result must be parseable as valid Python
        ast.parse(result)

    def test_does_not_introduce_await_when_absent(self):
        source = 'x = 1 + 2\n'
        result = source_preprocessor(source)
        # Semantically equivalent: same AST dump
        assert ast.dump(ast.parse(source)) == ast.dump(ast.parse(result))

    def test_handles_await_without_call(self):
        source = 'def f():\n    x = await coro_obj\n'
        result = source_preprocessor(source)
        assert not _has_bare_await(result)
        assert '__await_sync__' in result

    def test_handles_nested_await(self):
        source = 'def f():\n    x = await outer(await inner())\n'
        result = source_preprocessor(source)
        assert not _has_bare_await(result)
        assert result.count('__await_sync__') == 2

    def test_idempotent(self):
        source = 'def f():\n    x = await coro()\n'
        once = source_preprocessor(source)
        twice = source_preprocessor(once)
        assert once == twice


class TestAwaitSyncTransformer:
    def test_sync_function_runs_coroutine(self):
        async def coro():
            return 42

        tree = _transform('def f():\n    return await coro()\n')
        bytecode = compile(tree, '<string>', 'exec')
        ns = {'coro': coro}
        exec(bytecode, ns)
        assert ns['f']() == 42

    def test_runner_import_injected(self):
        tree = _transform('def f():\n    return await coro()\n')
        first_node = tree.body[0]
        assert isinstance(first_node, ast.ImportFrom)
        assert first_node.module == '__transformers__.await_sync'
        assert first_node.names[0].name == '_run_coro'
        assert first_node.names[0].asname == '__await_sync_run__'

    def test_no_import_injected_when_no_sync_await(self):
        code = 'async def f():\n    return await coro()\n'
        source = source_preprocessor(code)
        tree = ast.parse(source)
        tree = transformer.visit(tree)
        assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body)

    def test_async_function_keeps_real_await(self):
        code = 'async def f():\n    return await coro()\n'
        source = source_preprocessor(code)
        tree = ast.parse(source)
        tree = transformer.visit(tree)
        func_body = tree.body[-1].body
        ret = func_body[0]
        assert isinstance(ret.value, ast.Await)

    def test_sync_function_produces_runner_call(self):
        code = 'def f():\n    return await coro()\n'
        tree = _transform(code)
        # The function body is after the injected import
        func_def = tree.body[1]
        ret = func_def.body[0]
        assert isinstance(ret.value, ast.Call)
        assert isinstance(ret.value.func, ast.Name)
        assert ret.value.func.id == '__await_sync_run__'

    def test_end_to_end_sync_calls_coroutine(self):
        async def add(a, b):
            return a + b

        code = 'def compute():\n    return await add(3, 4)\n'
        tree = _transform(code)
        bytecode = compile(tree, '<string>', 'exec')
        ns = {'add': add}
        exec(bytecode, ns)
        assert ns['compute']() == 7

    def test_end_to_end_async_function_unaffected(self):
        async def double(x):
            return x * 2

        code = 'async def f():\n    return await double(5)\n'
        source = source_preprocessor(code)
        tree = ast.parse(source)
        tree = transformer.visit(tree)
        bytecode = compile(tree, '<string>', 'exec')
        ns = {'double': double}
        exec(bytecode, ns)
        assert asyncio.run(ns['f']()) == 10

    def test_nested_sync_inside_async_transforms_correctly(self):
        code = (
            'async def outer():\n'
            '    def inner():\n'
            '        return await fetch()\n'
            '    return inner()\n'
        )
        tree = _transform(code)
        async_func = tree.body[1]   # async def outer (after injected import)
        inner_func = async_func.body[0]  # def inner
        assert isinstance(inner_func, ast.FunctionDef)
        ret = inner_func.body[0]
        assert isinstance(ret.value, ast.Call)
        assert isinstance(ret.value.func, ast.Name)
        assert ret.value.func.id == '__await_sync_run__'

    def test_works_when_called_from_running_loop(self):
        async def fetch():
            return 7

        code = 'def sync_helper():\n    return await fetch()\n'
        tree = _transform(code)
        bytecode = compile(tree, '<string>', 'exec')
        ns = {'fetch': fetch}
        exec(bytecode, ns)

        async def driver():
            return ns['sync_helper']()  # sync — no await

        assert asyncio.run(driver()) == 7
