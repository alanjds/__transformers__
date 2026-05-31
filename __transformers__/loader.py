import ast
from importlib import import_module
from importlib.machinery import PathFinder, SourceFileLoader
import re
import sys

_TRANSFORMER_IMPORT_RE = re.compile(
    r'from\s+__transformers__\s+import\s+([\w\s,]+)'
)


def _get_source_transformer_names(source_str):
    names = []
    for match in _TRANSFORMER_IMPORT_RE.finditer(source_str):
        for name in match.group(1).split(','):
            name = name.strip()
            if name and name not in ('_loader', 'setup'):
                names.append(name)
    return names


class NodeVisitor(ast.NodeVisitor):
    def __init__(self):
        self._found = []

    def visit_ImportFrom(self, node):
        if node.module == '__transformers__':
            self._found += [name.name for name in node.names
                            if name.name not in ('_loader', 'setup')]

    @classmethod
    def get_transformers(cls, tree):
        visitor = cls()
        visitor.visit(tree)
        return visitor._found


def transform(tree):
    """Apply transformations to ast."""
    transformers = NodeVisitor.get_transformers(tree)

    for module_name in transformers:
        module = import_module('.{}'.format(module_name), '__transformers__')
        tree = module.transformer.visit(tree)

    return tree


class Finder(PathFinder):
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        spec = super(Finder, cls).find_spec(fullname, path, target)
        if spec is None:
            return None

        spec.loader = Loader(spec.loader.name, spec.loader.path)
        return spec


class Loader(SourceFileLoader):
    def source_to_code(self, data, path, *, _optimize=-1):
        source = data.decode('utf-8') if isinstance(data, bytes) else data
        for name in _get_source_transformer_names(source):
            try:
                module = import_module('.{}'.format(name), '__transformers__')
                if hasattr(module, 'source_preprocessor'):
                    source = module.source_preprocessor(source)
            except ImportError:
                pass
        data = source.encode('utf-8') if isinstance(data, bytes) else source
        tree = ast.parse(data)
        tree = transform(tree)
        return compile(tree, path, 'exec',
                       dont_inherit=True, optimize=_optimize)


def setup():
    sys.meta_path.insert(0, Finder)
