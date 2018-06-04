import pytest
import inspect
import textwrap
import ast
import astor
import functools
import magma
from magma.simulator.coreir_simulator import CoreIRSimulator
from magma.scope import Scope
from bit_vector import BitVector
import random

global_type_table = {}

def get_ast(obj):
    indented_program_txt = inspect.getsource(obj)
    program_txt = textwrap.dedent(indented_program_txt)
    return ast.parse(program_txt)

class Type:
    pass

class Random(Type):
    def __init__(self, width):
        self.width = width


class RandomStrategy:
    pass

class Uniform(RandomStrategy):
    pass


class CollectTypes(ast.NodeVisitor):
    def __init__(self, defn_globals, defn_locals):
        self.type_table = {}
        self.defn_globals = defn_globals
        self.defn_locals = defn_locals

    def visit_FunctionDef(self, node):
        for arg in node.args.args:
            self.type_table[arg.arg] = eval(compile(ast.Expression(arg.annotation), filename="<ast>", mode="eval"), self.defn_globals, self.defn_locals)
        for statement in node.body:
            self.visit(statement)

    # def visit_Assign(self, node):
    #     if isinstance(node.targets[0], ast.Attribute) and \
    #             node.targets[0].value.id in self.type_table and \
    #             self.type_table[node.targets[0].value.id] is magma.circuit.CircuitKind:
    #         # Assigning to attribute of a circuit, defer type check to later
    #         return
    #     print(ast.dump(node))
    #     result = eval(compile(ast.Expression(node.value), filename="<ast>",
    #                           mode="eval"), self.defn_locals)
    #     if isinstance(result, magma.circuit.CircuitKind):
    #         self.type_table[node.targets[0].id] = magma.circuit.CircuitKind
    #     else:
    #         raise NotImplementedError(type(result))

def collect_types(tree, defn_globals, defn_locals):
    type_collector = CollectTypes(defn_globals, defn_locals)
    type_collector.visit(tree)
    return type_collector.type_table


class CoreIRSimulatorRewrite(ast.NodeTransformer):
    def __init__(self, sim_object):
        super().__init__()
        self.sim_object = sim_object

    def visit_Assign(self, node):
        target = node.targets[0].attr
        return ast.parse(f"{self.sim_object}.set_value('{target}', {astor.to_source(node.value).rstrip()}, Scope())").body[0]

    def visit_Attribute(self, node):
        attr = node.attr
        if attr == "eval":
            node.attr = "evaluate"
            return node
        return ast.parse(f"{self.sim_object}.get_value('{attr}', Scope())").body[0].value

    def visit_FunctionDef(self, node):
        node.body = [self.visit(s) for s in node.body]
        return node


class FaultSimulator:
    def __init__(self, circuit, clk):
        self.circuit = circuit
        self.coreir_sim = CoreIRSimulator(circuit, clk)

    def evaluate(self):
        self.coreir_sim.evaluate()

    def set_value(self, port, value, scope):
        self.coreir_sim.set_value(getattr(self.circuit, port), value, scope)

    def get_value(self, port, scope):
        return self.coreir_sim.get_value(getattr(self.circuit, port), scope)


def test_case(combinational=False, random_strategy : RandomStrategy = None,
              num_tests=16):
    def test_wrapper(fn):
        stack = inspect.stack()
        defn_locals = stack[1].frame.f_locals
        defn_globals = stack[1].frame.f_globals
        tree = get_ast(fn)
        type_table = collect_types(tree, defn_globals, defn_locals)
        tree.body[0].decorator_list = []
        for key, value in type_table.items():
            if isinstance(value, Random):
                width = value.width
                N = 1 << width
                arg = str([BitVector(random.randint(0, N - 1), width) for _ in
                           range(num_tests)])
            else:
                arg = f"[{value}]"
                defn_globals[str(value)] = FaultSimulator(value, None)
                sim_object = str(value)
            tree.body[0].decorator_list.append(
                ast.parse(
                    f"pytest.mark.parametrize('{key}', {arg})"
                ).body[0].value
            )

        tree = CoreIRSimulatorRewrite(sim_object).visit(tree)

        # print(type_table)
        print(astor.to_source(tree))
        defn_globals["pytest"] = pytest
        defn_globals["BitVector"] = BitVector
        defn_globals["Scope"] = Scope
        result = exec(compile(tree, filename="<ast>", mode="exec"), defn_globals, defn_locals)
        return functools.wraps(fn)(eval(fn.__name__, defn_globals, defn_locals))
    return test_wrapper

def pytest_generate_tests(metafunc):
    pass

