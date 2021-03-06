try:
    from Crypto.Hash import keccak
    sha3_256 = lambda x: keccak.new(digest_bits=256, data=x).digest()
except ImportError:
    import sha3 as _sha3
    sha3_256 = lambda x: _sha3.sha3_256(x).digest()

import ast, tokenize, binascii
from io import BytesIO
from opcodes import opcodes, pseudo_opcodes

# Converts code to parse tree
def parse(code):
    return ast.parse(code).body

def parse_line(code):
    return parse(code)[0].value

# Converts for bytes to an integer
def fourbytes_to_int(inp):
    return (inp[0] << 24) + (inp[1] << 16) + (inp[2] << 8) + inp[3]

# Converts a provided hex string to an integer
def hex_to_int(inp):
    if inp[:2] == '0x':
        inp = inp[2:]
    bytez = binascii.unhexlify(inp)
    o = 0
    for b in bytez:
        o = o * 256 + b
    return o

# Data structure for LLL parse tree
class LLLnode():
    def __init__(self, value, args=[], typ=None, annotation=None):
        self.value = value
        self.args = args
        self.typ = typ
        self.annotation = annotation
        # Determine this node's valency (1 if it pushes a value on the stack,
        # 0 otherwise) and checks to make sure the number and valencies of
        # children are correct
        # Numbers
        if isinstance(self.value, int):
            self.valency = 1
        elif isinstance(self.value, str):
            # Opcodes and pseudo-opcodes (eg. clamp)
            if self.value.upper() in opcodes or self.value.upper() in pseudo_opcodes:
                record = opcodes.get(self.value.upper(), pseudo_opcodes.get(self.value.upper(), None))
                self.valency = record[2]
                if len(self.args) != record[1]:
                    raise Exception("Number of arguments mismatched: %r %r" % (self.value, self.args))
                for arg in self.args:
                    if arg.valency == 0:
                        raise Exception("Can't have a zerovalent argument to an opcode or a pseudo-opcode! %r" % arg)
            # If statements
            elif self.value == 'if':
                if len(self.args) == 3:
                    if self.args[1].valency != self.args[2].valency:
                        raise Exception("Valency mismatch between then and else clause: %r %r" % (self.args[1], self.args[2]))
                if len(self.args) == 2 and self.args[1].valency:
                    raise Exception("2-clause if statement must have a zerovalent body: %r" % self.args[1])
                if not self.args[0].valency:
                    raise Exception("Can't have a zerovalent argument as a test to an if statement! %r" % self.args[0])
                if len(self.args) not in (2, 3):
                    raise Exception("If can only have 2 or 3 arguments")
                self.valency = self.args[1].valency
            # With statements: with <var> <initial> <statement>
            elif self.value == 'with':
                if len(self.args) != 3:
                    raise Exception("With statement must have 3 arguments")
                if len(self.args[0].args) or not isinstance(self.args[0].value, str):
                    raise Exception("First argument to with statement must be a variable")
                if not self.args[1].valency:
                    raise Exception("Second argument to with statement (initial value) cannot be zerovalent: %r" % self.args[1])
                self.valency = self.args[2].valency
            # Repeat statements: repeat <index_memloc> <startval> <rounds> <body>
            elif self.value == 'repeat':
                if len(self.args[2].args) or not isinstance(self.args[2].value, int) or self.args[2].value <= 0:
                    raise Exception("Number of times repeated must be a constant nonzero positive integer")
                if not self.args[0].valency:
                    raise Exception("First argument to repeat (memory location) cannot be zerovalent: %r" % self.args[0])
                if not self.args[1].valency:
                    raise Exception("Second argument to repeat (start value) cannot be zerovalent: %r" % self.args[1])
                if self.args[3].valency:
                    raise Exception("Third argument to repeat (clause to be repeated) must be zerovalent: %r" % self.args[3])
                self.valency = 0
            # Seq statements: seq <statement> <statement> ...
            elif self.value == 'seq':
                self.valency = self.args[-1].valency if self.args else 0
            # Variables
            else:
                self.valency = 1
        else:
            raise Exception("Invalid value for LLL AST node: %r" % self.value)
        assert isinstance(self.args, list)

    def to_list(self):
        return [self.value] + [a.to_list() for a in self.args]

    def repr(self):
        x = repr(self.to_list())
        if len(x) < 80:
            return x
        o = '[' + repr(self.value) + ',\n  '
        for arg in self.args:
            sub = arg.repr().replace('\n', '\n  ').strip(' ')
            o += sub + '\n  '
        return o.rstrip(' ') + ']'

    def __repr__(self):
        return self.repr()

    @classmethod
    def from_list(cls, obj, typ=None, annotation=None):
        if isinstance(obj, LLLnode):
            return obj
        elif not isinstance(obj, list):
            return cls(obj, [], typ, annotation)
        else:
            return cls(obj[0], [cls.from_list(o) for o in obj[1:]], typ, annotation)

# Available base types
types = ['num', 'decimal', 'bytes32', 'num256', 'signed256', 'bool', 'address']

# Available types that functions can have as inputs
allowed_func_input_types = ['num', 'bool', 'num256', 'signed256', 'address']

# Available types that functions can have as outputs
allowed_func_output_types = ['num', 'bool', 'num256', 'signed256', 'address']

# A decimal value can store multiples of 1/DECIMAL_DIVISOR
DECIMAL_DIVISOR = 10000000000

# Number of bytes in memory used for system purposes, not for variables
RESERVED_MEMORY = 256
ADDRSIZE_POS = 32
MAXNUM_POS = 64
MINNUM_POS = 96
MAXDECIMAL_POS = 128
MINDECIMAL_POS = 160

# Convert type into common form used in ABI
def canonicalize_type(t):
    if t == 'num':
        return 'int128'
    elif t == 'bool':
        return 'bool'
    elif t == 'num256':
        return 'int256'
    elif t == 'signed256':
        return 'uint256'
    elif t == 'address' or t == 'bytes32':
        return t
    elif t == 'real':
        return 'real128x128'
    raise Exception("Invalid or unsupported type: "+repr(t))

# Cannot be used for variable naming
reserved_words = ['int128', 'int256', 'uint256', 'address', 'bytes32',
                  'real', 'real128x128', 'if', 'for', 'while', 'until',
                  'pass', 'def', 'push', 'dup', 'swap', 'send', 'call',
                  'suicide', 'selfdestruct', 'assert', 'stop', 'throw',
                  'raise', 'init', '_init_', '___init___', '____init____']

# Is a variable name valid?
def is_varname_valid(varname):
    if varname.lower() in types:
        return False
    if varname.lower() in reserved_words:
        return False
    if varname[0] == '~':
        return False
    if varname.upper() in opcodes:
        return False
    return True

class InvalidTypeException(Exception):
    pass

# Parses an expression representing a type. Annotation refers to whether
# the type is to be located in memory or storage
def parse_type(item, annotation):
    # Base types, eg. uint
    if isinstance(item, ast.Name):
        if item.id not in types:
            raise InvalidTypeException("Invalid type: "+item.id)
        return item.id
    # Lists, used to enumerate members, eg. [bar(uint), baz(bytes32)...]
    elif isinstance(item, ast.List):
        all_calls = True
        for elt in item.elts:
            if not isinstance(elt, ast.Call):
                all_calls = False
                break
            elif len(elt.args) != 1:
                raise InvalidTypeException("Named element must have single type in brackets")
        if len(item.elts) and all_calls:
            o = {elt.func.id: parse_type(elt.args[0], annotation) for elt in item.elts}
            if len(o) < len(item.elts):
                raise InvalidTypeException("Duplicate argument")
            return o
        else:
            raise InvalidTypeException("Mal-formatted list type")
    # Subscripts, used to represent fixed-size lists, eg. uint[100]
    elif isinstance(item, ast.Subscript):
        if not isinstance(item.slice.value, ast.Num):
            raise InvalidTypeException("Arrays must be of the format type[num_of_elements]")
        return [parse_type(item.value, annotation), item.slice.value.n]
    # Dicts, used to represent mappings, eg. {uint: uint}. Key must be a base type
    elif isinstance(item, ast.Dict):
        if annotation == 'memory':
            raise InvalidTypeException("No dicts allowed for in-memory types!") 
        if not (len(item.keys) == len(item.values) == 1):
            raise InvalidTypeException("Dict types must specify one mapping")
        if not isinstance(item.keys[0], ast.Name) or item.keys[0].id not in types:
            raise InvalidTypeException("Key type invalid")
        return {item.keys[0].id: parse_type(item.values[0], annotation)}
    else:
        raise InvalidTypeException("Invalid type: %r" % ast.dump(item))

# Gets the number of memory or storage keys needed to represent a given type
def get_size_of_type(typ):
    if not isinstance(typ, (list, dict)):
        return 1
    if isinstance(typ, list):
        return get_size_of_type(typ[0]) * typ[1]
    elif isinstance(typ, dict):
        if len(typ.keys()) == 1 and list(typ.keys())[0] in types:
            raise Exception("Type size infinite!")
        else:
            return sum([get_size_of_type(v) for v in typ.values()])

# Parse top-level functions and variables
def get_defs_and_globals(code):
    _globals = {}
    _defs = []
    for item in code:
        if isinstance(item, ast.Assign):
            if len(item.targets) != 1:
                raise Exception("Top-level assign must have one target")
            if item.targets[0].id in _globals:
                raise Exception("Cannot declare a persistent variable twice!")
            if len(_defs):
                raise Exception("Global variables must all come before function definitions")
            _globals[item.targets[0].id] = (len(_globals), parse_type(item.value, 'storage'))
        elif isinstance(item, ast.FunctionDef):
            _defs.append(item)
        else:
            raise Exception("Invalid top-level statement")
    return _defs, _globals

# Header code
def mk_initial():
    return LLLnode.from_list(['seq',
                                ['mstore', 28, ['calldataload', 0]],
                                ['mstore', ADDRSIZE_POS, 2**160],
                                ['mstore', MAXNUM_POS, 2**128 - 1],
                                ['mstore', MINNUM_POS, -2**128 + 1],
                                ['mstore', MAXDECIMAL_POS, (2**128 - 1) * DECIMAL_DIVISOR],
                                ['mstore', MINDECIMAL_POS, (-2**128 + 1) * DECIMAL_DIVISOR],
                             ], typ='null')

# Get function details
def get_func_details(code):
    name = code.name
    # Determine the arguments, expects something of the form def foo(arg1: num, arg2: num ...
    args = []
    for arg in code.args.args:
        typ = arg.annotation
        if not isinstance(arg.arg, (str, bytes)):
            raise Exception("Argument name invalid")
        if not typ:
            raise Exception("Argument must have type")
        if not isinstance(typ, ast.Name) or typ.id not in allowed_func_input_types:
            raise Exception("Argument type invalid or unsupported")
        if not is_varname_valid(arg.arg):
            raise Exception("Argument name invalid or reserved: "+arg.arg)
        if name == '__init__':
            args.append((arg.arg, -32 * len(code.args.args) + 32 * len(args), typ.id))
        else:
            args.append((arg.arg, 4 + 32 * len(args), typ.id))
        if typ.id not in allowed_func_input_types:
            raise Exception("Disallowed type for function inputs: "+typ.id)
    # Determine the return type and whether or not it's constant. Expects something
    # of the form:
    # def foo(): ...
    # def foo() -> num: ... 
    # def foo() -> num(const): ...
    const = False
    if not code.returns:
        output_type = None
    elif isinstance(code.returns, ast.Name) and code.returns.id in allowed_func_output_types:
        output_type = code.returns.id
    elif isinstance(code.returns, ast.Call) and isinstance(code.returns.func, ast.Name) and \
            code.returns.func.id in allowed_func_output_types and len(code.returns.args) == 1 and \
            isinstance(code.returns.args[0], ast.Name) and code.returns.args[0].id == 'const':
        output_type = code.returns.func.id
        const = True
    else:
        raise Exception("Output type invalid or unsupported: %r" % code.returns)
    # Get the four-byte method id
    sig = name + '(' + ','.join([canonicalize_type(arg.annotation.id) for arg in code.args.args]) + ')'
    method_id = fourbytes_to_int(sha3_256(bytes(sig, 'utf-8'))[:4])
    return name, args, output_type, const, sig, method_id

# Contains arguments, variables, etc
class Context():
    def __init__(self, args=None, vars=None, globals=None, forvars=None, return_type=None):
        self.args = args or {}
        self.vars = vars or {}
        self.globals = globals or {}
        self.forvars = forvars or {}
        self.return_type = return_type

    def new_variable(self, name, typ):
        if not is_varname_valid(name):
            raise Exception("Variable name invalid or reserved: "+name)
        if name in self.vars or name in self.args or name in self.globals:
            raise Exception("Duplicate variable name")
        pos = self.vars.get('_next_mem', RESERVED_MEMORY)
        self.vars[name] = pos, typ
        self.vars['_next_mem'] = pos + 32 * get_size_of_type(typ)
        return pos

# Is a function the initializer?
def is_initializer(code):
    return code.name == '__init__'

# Parses a function declaration
def parse_func(code, _globals, _vars=None):
    name, args, output_type, const, sig, method_id = get_func_details(code)
    context = Context(args={a[0]: (a[1], a[2]) for a in args}, globals=_globals, vars=_vars or {}, return_type=output_type)
    if name == '__init__':
        return parse_body(code.body, context)
    else:
        return LLLnode.from_list(['if',
                                    ['eq', ['mload', 0], method_id],
                                    ['seq'] + [parse_body(c, context) for c in code.body]
                                 ], typ='null')

# Get ABI signature
def mk_full_signature(code):
    o = []
    _defs, _globals = get_defs_and_globals(code)
    for code in _defs:
        name, args, output_type, const, sig, method_id = get_func_details(code)
        o.append({
            "name": sig,
            "outputs": [{"type": canonicalize_type(output_type), "name": "out"}] if output_type else [],
            "inputs": [{"type": canonicalize_type(typ), "name": nam} for nam, loc, typ in args],
            "constant": const,
            "type": "constructor" if name == "__init__" else "function"
        })
    return o
        
# Main python parse tree => LLL method
def parse_tree_to_lll(code):
    _defs, _globals = get_defs_and_globals(code)
    if len(set([_def.name for _def in _defs])) < len(_defs):
        raise Exception("Duplicate function name!")
    # Initialization function
    initfunc = [_def for _def in _defs if is_initializer(_def)]
    # Regular functions
    otherfuncs = [_def for _def in _defs if not is_initializer(_def)]
    if not initfunc and not otherfuncs:
        return LLLnode.from_list('pass')
    if not initfunc and otherfuncs:
        return LLLnode.from_list(['return', 0, ['lll', ['seq', mk_initial()] + [parse_func(_def, _globals) for _def in otherfuncs], 0]], typ=None)
    elif initfunc and not otherfuncs:
        return LLLnode.from_list(['seq', mk_initial(), parse_func(initfunc[0], _globals), ['selfdestruct']], typ=None)
    elif initfunc and otherfuncs:
        return LLLnode.from_list(['seq', mk_initial(), parse_func(initfunc[0], _globals),
                                    ['return', 0, ['lll', ['seq', mk_initial()] + [parse_func(_def, _globals) for _def in otherfuncs], 0]]],
                                 typ=None)
    
# Parse a piece of code
def parse_body(code, context):
    if not isinstance(code, list):
        return parse_stmt(code, context)
    o = []
    for stmt in code:
        o.append(parse_stmt(stmt, context))
    return LLLnode.from_list(['seq'] + o)

# Parse an expression that represents an address in memory or storage
def parse_left_expr(expr, context, type_hint=None):
    # Base type
    if isinstance(expr, ast.Name):
        if expr.id == 'self':
            return LLLnode.from_list(['address'], typ='address', annotation=None)
        # New variable
        if expr.id not in context.vars:
            pos = context.new_variable(expr.id, type_hint)
            assert type_hint # TODO
            return LLLnode.from_list(pos,
                                     typ=type_hint,
                                     annotation='memory')
        # Already declared variable
        else:
            pos, typ = context.vars[expr.id]
            return LLLnode.from_list(pos, typ=typ, annotation='memory')
    # Attribute of a variable, or attribute of "self" for a storage variable
    elif isinstance(expr, ast.Attribute):
        # x.balance: balance of address x
        if expr.attr == 'balance':
            addr = parse_expr(expr.value, context)
            if addr.typ != 'address':
                raise Exception("Can only get balance of an address")
            return LLLnode.from_list(['balance', addr], typ='num', annotation=None)
        # self.x: global attribute
        elif isinstance(expr.value, ast.Name) and expr.value.id == "self":
            if expr.attr not in context.globals:
                raise Exception("Persistent variable undeclared: "+expr.attr)
            pos, typ = context.globals[expr.attr][0],context.globals[expr.attr][1]
            return LLLnode.from_list(pos, typ=typ, annotation='storage')
        # Reserved keywords
        elif isinstance(expr.value, ast.Name) and expr.value.id in ("msg", "block", "tx"):
            key = expr.value.id + "." + expr.attr
            if key == "msg.sender":
                return LLLnode.from_list(['caller'], typ='address')
            elif key == "msg.value":
                return LLLnode.from_list(['callvalue'], typ='num')
            elif key == "block.difficulty":
                return LLLnode.from_list(['difficulty'], typ='num')
            elif key == "block.timestamp":
                return LLLnode.from_list(['timestamp'], typ='num')
            elif key == "block.number":
                return LLLnode.from_list(['number'], typ='num')
            elif key == "tx.origin":
                return LLLnode.from_list(['origin'], typ='address')
            else:
                raise Exception("Unsupported keyword: "+key)
        # Other variables
        else:
            sub = parse_left_expr(expr.value, context, type_hint)
            if not isinstance(sub.typ, dict):
                raise Exception("Type mismatch: member variable access not expected")
            attrs = sorted(sub.typ.keys())
            if expr.attr not in attrs:
                raise Exception("Member %s not found. Only the following available: %s" % (expr.attr, " ".join(attrs)))
            index = attrs.index(expr.attr)
            if sub.annotation == 'storage':
                return LLLnode.from_list(['add', ['sha3_32', sub], index],
                                         typ=sub.typ[expr.attr],
                                         annotation='storage')
            elif sub.annotation == 'memory':
                offset = 0
                for i in range(index):
                    offset += 32 * get_size_of_type(sub.typ[attrs[i]])
                return LLLnode.from_list(['add', offset, sub],
                                         typ=sub.typ[expr.attr],
                                         annotation='memory')
            else:
                raise Exception("Annotation weird: "+sub.annotation)
    # Subscript, eg. x[4]
    elif isinstance(expr, ast.Subscript):
        sub = parse_left_expr(expr.value, context, type_hint)
        index = parse_expr(expr.slice.value, context)
        if isinstance(sub.typ, list):
            subtype, itemcount = sub.typ[0], sub.typ[1]
            if sub.annotation == 'storage':
                return LLLnode.from_list(['add',
                                            ['sha3_32', sub],
                                            ['uclamplt', index, itemcount]],
                                         typ=subtype,
                                         annotation='storage')
            elif sub.annotation == 'memory':
                offset = 32 * get_size_of_type(subtype)
                return LLLnode.from_list(['add',
                                            ['mul', offset, ['uclamplt', index, itemcount]],
                                            sub],
                                          typ=subtype,
                                          annotation='memory')
            else:
                raise Exception("Annotation weird: "+sub.annotation)
        elif isinstance(sub.typ, dict):
            if sub.annotation == 'memory':
                raise Exception("Cannot use dicts for in-memory types: %r" % sub)
            return LLLnode.from_list(['add', ['sha3_32', sub], parse_expr(expr.slice.value, context)],
                                      typ=list(sub.typ.values())[0],
                                      annotation=sub.annotation)
        else:
            raise Exception("Type mismatch: array access not expected. Expr type: "+repr(sub.typ))
    else:
        raise Exception("Left-side expression invalid")

# Parse an expression
def parse_expr(expr, context):
    if isinstance(expr, LLLnode):
        return expr
    # Numbers (integers or decimals)
    elif isinstance(expr, ast.Num):
        if isinstance(expr.n, int):
            if not (-2**127 + 1 <= expr.n <= 2**127 - 1):
                raise Exception("Number out of range: "+str(expr.n))
            return LLLnode.from_list(expr.n, typ='num')
        elif isinstance(expr.n, float):
            if not (-2**127 + 1 <= expr.n <= 2**127 - 1):
                raise Exception("Number out of range: "+str(expr.n))
            return LLLnode.from_list(int(expr.n * DECIMAL_DIVISOR), typ='decimal')
    # Addresses and bytes32 objects
    elif isinstance(expr, ast.Str):
        if len(expr.s) == 42 and expr.s[:2] == '0x':
            return LLLnode.from_list(hex_to_int(expr.s), typ='address')
        elif len(expr.s) == 66 and expr.s[:2] == '0x':
            return LLLnode.from_list(hex_to_int(expr.s), typ='bytes32')
        else:
            raise Exception("Unsupported bytes: "+expr.s)
    # Variable names
    elif isinstance(expr, ast.Name):
        if expr.id == 'self':
            return LLLnode.from_list(['address'], typ='address')
        if expr.id in context.args:
            dataloc, typ = context.args[expr.id]
            if dataloc >= 0:
                data_decl = ['calldataload', dataloc]
            else:
                data_decl = ['seq', ['codecopy', 192, ['sub', ['codesize'], -dataloc], 32], ['mload', 192]]
            if typ == 'num':
                return LLLnode.from_list(['clamp', ['mload', MINNUM_POS], data_decl, ['mload', MAXNUM_POS]], typ='num')
            elif typ == 'bool':
                return LLLnode.from_list(['uclamplt', data_decl, 2], typ='bool')
            elif typ == 'address':
                return LLLnode.from_list(['uclamplt', data_decl, ['mload', ADDRSIZE_POS]], typ='address')
            elif typ == 'num256' or typ == 'signed256' or typ == 'bytes32':
                return LLLnode.from_list(data_decl, typ=typ)
            else:
                raise Exception("Unsupported type: "+typ)
        elif expr.id in context.vars:
            dataloc, typ = context.vars[expr.id]
            return LLLnode.from_list(['mload', dataloc], typ=typ)
        else:
            raise Exception("Undeclared variable: "+expr.id)
    # x.y or x[5]
    elif isinstance(expr, (ast.Subscript, ast.Attribute)):
        o = parse_left_expr(expr, context)
        if o.annotation == 'memory':
            return LLLnode.from_list(['mload', o], typ=o.typ)
        elif o.annotation == 'storage':
            return LLLnode.from_list(['sload', o], typ=o.typ)
        else:
            return o
    # Arithmetic operations
    elif isinstance(expr, ast.BinOp):
        left = parse_expr(expr.left, context)
        right = parse_expr(expr.right, context)
        for typ in (left.typ, right.typ):
            if typ not in ('num', 'decimal'):
                raise Exception("Invalid type for arithmetic op: "+typ)
        if isinstance(expr.op, (ast.Add, ast.Sub)):
            op = 'add' if isinstance(expr.op, ast.Add) else 'sub'
            if left.typ == right.typ:
                o = LLLnode.from_list([op, left, right], typ=left.typ)
            elif left.typ == 'num' and right.typ == 'decimal':
                o = LLLnode.from_list([op, ['mul', left, DECIMAL_DIVISOR], right], typ='decimal')
            elif right.typ == 'decimal' and right.typ == 'num':
                o = LLLnode.from_list([op, left, ['mul', right, DECIMAL_DIVISOR]], typ='decimal')
        elif isinstance(expr.op, ast.Mult):
            if left.typ == right.typ == 'num':
                o = LLLnode.from_list(['mul', left, right], typ='num')
            elif left.typ == right.typ == 'decimal':
                o = LLLnode.from_list(['with', 'r', right, ['with', 'l', left,
                                        ['with', 'ans', ['mul', 'l', 'r'],
                                            ['seq',
                                                ['assert', ['or', ['eq', ['sdiv', 'ans', 'l'], 'r'], ['not', 'l']]],
                                                ['sdiv', 'ans', DECIMAL_DIVISOR]]]]], typ='decimal')
            elif (left.typ == 'num' and right.typ == 'decimal') or (left.typ == 'decimal' and right.typ == 'num'):
                o = LLLnode.from_list(['with', 'r', right, ['with', 'l', left,
                                        ['with', 'ans', ['mul', 'l', 'r'],
                                            ['seq',
                                                ['assert', ['or', ['eq', ['sdiv', 'ans', 'l'], 'r'], ['not', 'l']]],
                                                'ans']]]], typ='decimal')
        elif isinstance(expr.op, ast.Div):
            if right.typ == 'num':
                o = LLLnode.from_list(['sdiv', left, ['clamp_nonzero', right]], typ=left.typ)
            elif left.typ == right.typ == 'decimal':
                o = LLLnode.from_list(['with', 'l', left, ['with', 'r', ['clamp_nonzero', right],
                                            ['sdiv', ['mul', 'l', DECIMAL_DIVISOR], 'r']]],
                                      typ='decimal')
            elif left.typ == 'num' and right.typ == 'decimal':
                o = LLLnode.from_list(['sdiv', ['mul', left, DECIMAL_DIVISOR ** 2], ['clamp_nonzero', right]], typ='decimal')
        elif isinstance(expr.op, ast.Mod):
            if left.typ == right.typ:
                o = LLLnode.from_list(['smod', left, ['clamp_nonzero', right]], typ=left.typ)
            elif left.typ == 'decimal' and right.typ == 'num':
                o = LLLnode.from_list(['smod', left, ['mul', ['clamp_nonzero', right], DECIMAL_DIVISOR]], typ='decimal')
            elif left.typ == 'num' and right.typ == 'decimal':
                o = LLLnode.from_list(['smod', ['mul', left, DECIMAL_DIVISOR], right], typ='decimal')
        else:
            raise Exception("Unsupported binop: %r" % expr.op)
    # Comparison operations
    elif isinstance(expr, ast.Compare):
        left = parse_expr(expr.left, context)
        right = parse_expr(expr.comparators[0], context)
        if len(expr.ops) != 1:
            raise Exception("Cannot have a comparison with more than two elements")
        if isinstance(expr.ops[0], ast.Gt):
            op = 'sgt'
        elif isinstance(expr.ops[0], ast.GtE):
            op = 'sge'
        elif isinstance(expr.ops[0], ast.LtE):
            op = 'sle'
        elif isinstance(expr.ops[0], ast.Lt):
            op = 'slt'
        elif isinstance(expr.ops[0], ast.Eq):
            op = 'eq'
        elif isinstance(expr.ops[0], ast.NotEq):
            op = 'ne'
        else:
            raise Exception("Unsupported comparison operator")
        for typ in (left.typ, right.typ):
            if typ not in ('num', 'decimal'):
                if op not in ('eq', 'ne'):
                    raise Exception("Invalid type for comparison op: "+typ)
        if left.typ == right.typ:
            o = LLLnode.from_list([op, left, right], typ='bool')
        elif left.typ == 'decimal' and right.typ == 'num':
            o = LLLnode.from_list([op, left, ['mul', right, DECIMAL_DIVISOR]], typ='bool')
        elif left.typ == 'num' and right.typ == 'decimal':
            o = LLLnode.from_list([op, ['mul', left, DECIMAL_DIVISOR], right], typ='bool')
        else:
            raise Exception("Unsupported types for comparison: %r %r" % (left.typ, right.typ))
    # Boolean logical operations
    elif isinstance(expr, ast.BoolOp):
        if len(expr.values) != 2:
            raise Exception("Expected two arguments for a bool op")
        left = parse_expr(expr.values[0], context)
        right = parse_expr(expr.values[1], context)
        if left.typ != 'bool' or right.typ != 'bool':
            raise Exception("Boolean operations can only be between booleans!")
        if isinstance(expr.op, ast.And):
            op = 'and'
        elif isinstance(expr.op, ast.Or):
            op = 'or'
        else:
            raise Exception("Unsupported bool op: "+expr.op)
        o = LLLnode.from_list([op, left, right], typ='bool')
    # Unary operations (only "not" supported)
    elif isinstance(expr, ast.UnaryOp):
        operand = parse_expr(expr.operand, context)
        if not isinstance(expr.op, ast.Not):
            raise Exception("Only the 'not' unary operator is supported")
        # Note that in the case of bool, num, address, decimal, num256 AND bytes32,
        # a zero entry represents false, all others represent true
        o = LLLnode.from_list(["iszero", operand], typ='bool')
    # Function calls
    elif isinstance(expr, ast.Call): 
        if isinstance(expr.func, ast.Name) and expr.func.id == 'floor':
            if len(expr.args) != 1:
                raise Exception("Floor expects 1 argument!")
            sub = parse_expr(expr.args[0], context)
            if sub.typ in ('num', 'num256', 'signed256'):
                return sub
            elif sub.typ == 'decimal':
                return LLLnode.from_list(['sdiv', sub, DECIMAL_DIVISOR], typ='num')
            else:
                raise Exception("Bad type for argument to floor: %r" % sub.typ)
        elif isinstance(expr.func, ast.Name) and expr.func.id == 'decimal':
            if len(expr.args) != 1:
                raise Exception("Decimal expects 1 argument!")
            sub = parse_expr(expr.args[0], context)
            if sub.typ == 'decimal':
                return sub
            elif sub.typ == 'num':
                return LLLnode.from_list(['mul', sub, DECIMAL_DIVISOR], typ='decimal')
            else:
                raise Exception("Bad type for argument to decimal: %r" % sub.typ)
        else:
            raise Exception("Unsupported operator: %r" % ast.dump(expr))
    else:
        raise Exception("Unsupported operator: %r" % ast.dump(expr))
    # Clamp based on variable type
    if o.typ == 'bool':
        return o
    elif o.typ == 'num':
        return LLLnode.from_list(['clamp', ['mload', MINNUM_POS], o, ['mload', MAXNUM_POS]], typ='num')
    elif o.typ == 'decimal':
        return LLLnode.from_list(['clamp', ['mload', MINDECIMAL_POS], o, ['mload', MAXDECIMAL_POS]], typ='decimal')
    else:
        return o


# Convert from one type to another
def type_conversion(orig, frm, to):
    if frm == to and not isinstance(frm, (list, dict)):
        return orig
    elif frm == 'num' and to == 'decimal':
        return LLLnode.from_list(['mul', orig, DECIMAL_DIVISOR])
    elif isinstance(frm, (list, dict)):
        raise Exception("Directly setting non-atomic types is not supported")
    else:
        raise Exception("Typecasting from %r to %r unavailable" % (frm, to))


# Parse a statement (usually one line of code but not always)
def parse_stmt(stmt, context):
    if isinstance(stmt, ast.Expr):
        return parse_stmt(stmt.value, context)
    elif isinstance(stmt, ast.Pass):
        return LLLnode.from_list('pass', typ=None)
    elif isinstance(stmt, ast.Assign):
        # Assignment (eg. x[4] = y)
        if len(stmt.targets) != 1:
            raise Exception("Assignment statement must have one target")
        try:
            typ = parse_type(stmt.value, annotation='memory')
            if not isinstance(stmt.targets[0], ast.Name):
                raise Exception("Can only assign a variable to a new type")
            varname = stmt.targets[0].id
            pos = context.new_variable(varname, typ)
            return LLLnode.from_list('pass', typ=None)
        except InvalidTypeException:
            sub = parse_expr(stmt.value, context)
            target = parse_left_expr(stmt.targets[0], context, type_hint=sub.typ)
            sub = type_conversion(sub, sub.typ, target.typ)
            if target.annotation == 'storage':
                return LLLnode.from_list(['sstore', target, sub], typ=None)
            elif target.annotation == 'memory':
                return LLLnode.from_list(['mstore', target, sub], typ=None)
    # If statements
    elif isinstance(stmt, ast.If):
        if stmt.orelse:
            return LLLnode.from_list(['if',
                                      parse_expr(stmt.test, context),
                                      parse_body(stmt.body, context),
                                      parse_body(stmt.orelse[0], context)], typ=None)
        else:
            return LLLnode.from_list(['if',
                                      parse_expr(stmt.test, context),
                                      parse_body(stmt.body, context)], typ=None)
    # Calls
    elif isinstance(stmt, ast.Call):
        if not isinstance(stmt.func, ast.Name):
            raise Exception("Function call must be one of: send, selfdestruct")
        if stmt.func.id == 'send':
            if len(stmt.args) != 2:
                raise Exception("Send expects 2 arguments!")
            to = parse_expr(stmt.args[0], context)
            if to.typ != "address":
                raise Exception("Need to send to address")
            value = parse_expr(stmt.args[1], context)
            if value.typ != "num" and value.typ != "num256" and value.typ != "decimal":
                raise Exception("Send value must be a number!")
            if value.typ == "decimal":
                return LLLnode.from_list(['pop', ['call', 0, to, ['div', value, DECIMAL_DIVISOR], 0, 0, 0, 0]], typ=None)
            else:
                return LLLnode.from_list(['pop', ['call', 0, to, value, 0, 0, 0, 0]], typ=None)
        elif stmt.func.id in ('suicide', 'selfdestruct'):
            if len(stmt.args) != 1:
                raise Exception("%s expects 1 argument!" % stmt.func.id)
            sub = parse_expr(stmt.args[0], context)
            if sub.typ != "address":
                raise Exception("Must %s to an address!" % stmt.func.id)
            return LLLnode.from_list(['selfdestruct', sub], typ=None)
            
    elif isinstance(stmt, ast.Assert):
        return LLLnode.from_list(['assert', parse_expr(stmt.test, context)], typ=None)
    # for i in range(n): ... (note: n must be a nonzero positive constant integer)
    elif isinstance(stmt, ast.For):
        if not isinstance(stmt.iter, ast.Call) or \
                not isinstance(stmt.iter.func, ast.Name) or \
                not isinstance(stmt.target, ast.Name) or \
                stmt.iter.func.id != "range" or \
                len(stmt.iter.args) not in (1, 2):
            raise Exception("For statements must be of the form `for i in range(rounds): ..` or `for i in range(start, start + rounds): ..`")
        # Type 1 for, eg. for i in range(10): ...
        if len(stmt.iter.args) == 1:
            if not isinstance(stmt.iter.args[0], ast.Num):
                raise Exception("Repeat must have a nonzero positive integral number of rounds")
            start = LLLnode.from_list(0, typ='num')
            rounds = stmt.iter.args[0].n
        elif len(stmt.iter.args) == 2:
            if isinstance(stmt.iter.args[0], ast.Num) and isinstance(stmt.iter.args[1], ast.Num):
                # Type 2 for, eg. for i in range(100, 110): ...
                start = LLLnode.from_list(stmt.iter.args[0].n, typ='num')
                rounds = LLLnode.from_list(stmt.iter.args[1].n - stmt.iter.args[0].n, typ='num')
            else:
                # Type 3 for, eg. for i in range(x, x + 10): ...
                if not isinstance(stmt.iter.args[1], ast.BinOp) or not isinstance(stmt.iter.args[1].op, ast.Add):
                    raise Exception("Two-arg for statements must be of the form `for i in range(start, start + rounds): ...`")
                if ast.dump(stmt.iter.args[0]) != ast.dump(stmt.iter.args[1].left):
                    raise Exception("Two-arg for statements of the form `for i in range(x, x + y): ...` must have x identical in both places: %r %r" % (ast.dump(stmt.iter.args[0]), ast.dump(stmt.iter.args[1].left)))
                if not isinstance(stmt.iter.args[1].right, ast.Num):
                    raise Exception("Repeat must have a nonzero positive integral number of rounds")
                start = parse_expr(stmt.iter.args[0], context)
                rounds = stmt.iter.args[1].right.n
        varname = stmt.target.id
        pos = context.vars[varname][0] if varname in context.forvars else context.new_variable(varname, 'num')
        o = LLLnode.from_list(['repeat', pos, start, rounds, parse_body(stmt.body, context)], typ=None)
        context.forvars[varname] = True
        return o
    # Creating a new memory variable and assigning it
    elif isinstance(stmt, ast.AugAssign):
        sub = parse_expr(stmt.value, context)
        target = parse_left_expr(stmt.target, context)
        if not isinstance(stmt.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)):
            raise Exception("Unsupported operator for augassign")
        if target.annotation == 'storage':
            o = parse_expr(ast.BinOp(left=LLLnode.from_list(['sload', '_addr'], typ=target.typ),
                                     right=sub, op=stmt.op), context)
            return LLLnode.from_list(['with', '_addr', target, ['sstore', '_addr', type_conversion(o, o.typ, target.typ)]], typ=None)
        elif target.annotation == 'memory':
            o = parse_expr(ast.BinOp(left=LLLnode.from_list(['mload', '_addr'], typ=target.typ),
                                     right=sub, op=stmt.op), context)
            return LLLnode.from_list(['with', '_addr', target, ['mstore', '_addr', type_conversion(o, o.typ, target.typ)]], typ=None)
    # Break from a loop
    elif isinstance(stmt, ast.Break):
        return LLLnode.from_list('break', typ=None)
    # Return statement
    elif isinstance(stmt, ast.Return):
        if context.return_type is None:
            if stmt.value:
                raise Exception("Not expecting to return a value")
            return LLLnode.from_list(['return', 0, 0], typ=None)
        if not stmt.value:
            raise Exception("Expecting to return a value")
        sub = parse_expr(stmt.value, context)
        if sub.typ == context.return_type or (sub.typ == 'num' and context.return_type == 'signed256'):
            return LLLnode.from_list(['seq', ['mstore', 0, sub], ['return', 0, 32]], typ=None)
        elif sub.typ == 'num' and context.return_type == 'num256':
            return LLLnode.from_list(['seq', ['mstore', 0, sub],
                                             ['assert', ['iszero', ['lt', ['mload', 0], 0]]],
                                             ['return', 0, 32]], typ=None)
        else:
            raise Exception("Unsupported type conversion: %r %r" % (sub.typ, context.return_type))
    else:
        print(ast.dump(stmt))
        raise Exception("Unsupported statement type")
