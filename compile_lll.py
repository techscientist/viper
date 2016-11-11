from parser import LLLnode
from opcodes import opcodes

def num_to_bytearray(x):
    o = []
    while x > 0:
        o.insert(0, x % 256)
        x //= 256
    return o

PUSH_OFFSET = 0x5f
DUP_OFFSET = 0x7f
SWAP_OFFSET = 0x8f

def gas_estimate(code, depth=0):
    if isinstance(code.value, int):
        return 3
    elif isinstance(code.value, str) and code.value.upper() in opcodes:
        o = sum([gas_estimate(c, depth + i) for i, c in enumerate(code.args[::-1])]) + opcodes[code.value.upper()][3]
        # Dynamic gas costs
        if code.value.upper() == 'CALL' and code.args[2].value != 0:
            o += 34000
        if code.value.upper() == 'SSTORE' and code.args[1].value != 0:
            o += 15000
        return o
    elif isinstance(code.value, str) and code.value == 'if':
        if (len(code.args) == 2):
            return gas_estimate(code.args[0], depth + 1) + gas_estimate(code.args[1], depth + 1) + 30
        elif (len(code.args) == 3):
            return gas_estimate(code.args[0], depth + 1) + max(gas_estimate(code.args[1], depth + 1), gas_estimate(code.args[2], depth + 1)) + 30
        else:
            raise Exception("If statement must have 2 or 3 child elements")
    elif isinstance(code.value, str) and code.value == 'with':
        return gas_estimate(code.args[1], depth + 1) + gas_estimate(code.args[3], depth + 1) + 20
    elif isinstance(code.value, str) and code.value == 'clamp':
        return gas_estimate(code.args[0], depth + 1) + gas_estimate(code.args[1], depth + 1) + gas_estimate(code.args[2], depth + 1) + 50
    elif isinstance(code.value, str) and code.value == 'clamplt':
        return gas_estimate(code.args[0], depth + 1) + gas_estimate(code.args[1], depth + 1) + 30
    elif isinstance(code.value, str) and code.value == 'clamp_nonzero':
        return gas_estimate(code.args[0], depth + 1) + 20
    elif isinstance(code.value, str) and code.value == 'pass':
        return 0
    elif isinstance(code.value, str) and code.value == 'break':
        return 20 + opcodes['POP'][3] * depth
    elif isinstance(code.value, str) and code.value == 'repeat':
        return (gas_estimate(code.args[2], depth + 1) + 50) * code.args[0].value + 30
    elif isinstance(code.value, str) and code.value == 'seq':
        return sum([gas_estimate(c, depth + 1) for c in code.args])
    else:
        raise Exception("Gas estimate failed: "+repr(code))

next_symbol = [0]

def mksymbol():
    next_symbol[0] += 1
    return '_sym_'+str(next_symbol[0])

def is_symbol(i):
    return isinstance(i, str) and i[:5] == '_sym_'

def compile_to_assembly(code, withargs={}, break_dest=None, height=0):
    if isinstance(code.value, str) and code.value.upper() in opcodes:
        o = []
        for i, c in enumerate(code.args[::-1]):
            o.extend(compile_to_assembly(c, withargs, break_dest, height + i))
        o.append(code.value.upper())
        return o
    elif isinstance(code.value, int):
        if code.value <= -2**255:
            raise Exception("Value too low: %d" % code.value)
        elif code.value >= 2**256:
            raise Exception("Value too high: %d" % code.value)
        bytez = num_to_bytearray(code.value % 2**256) or [0]
        return ['PUSH'+str(len(bytez))] + bytez
    elif isinstance(code.value, str) and code.value in withargs:
        if height - withargs[code.value] > 16:
            raise Exception("With statement too deep")
        return ['DUP'+str(height - withargs[code.value])]
    elif code.value == 'pass':
        return []
    elif code.value == 'if' and len(code.args) == 2:
        o = []
        o.extend(compile_to_assembly(code.args[0], withargs, break_dest, height))
        end_symbol = mksymbol()
        o.extend(['ISZERO', end_symbol, 'JUMPI'])
        o.extend(compile_to_assembly(code.args[1], withargs, break_dest, height))
        o.extend([end_symbol, 'JUMPDEST'])
        return o
    elif code.value == 'if' and len(code.args) == 3:
        o = []
        o.extend(compile_to_assembly(code.args[0], withargs, break_dest, height))
        mid_symbol = mksymbol()
        end_symbol = mksymbol()
        o.extend(['ISZERO', mid_symbol, 'JUMPI'])
        o.extend(compile_to_assembly(code.args[1], withargs, break_dest, height))
        o.extend([end_symbol, 'JUMP', mid_symbol, 'JUMPDEST'])
        o.extend(compile_to_assembly(code.args[2], withargs, break_dest, height))
        o.extend([end_symbol, 'JUMPDEST'])
        return o
    elif code.value == 'repeat':
        o = []
        loops = num_to_bytearray(code.args[0].value) or [0]
        start, end = mksymbol(), mksymbol()
        o.extend(['PUSH'+str(len(loops))] + loops)
        o.extend(compile_to_assembly(code.args[1]))
        o.extend(['PUSH1', 0, 'DUP2', 'MSTORE', start, 'JUMPDEST'])
        # stack: len(loops), index memory address
        o.extend(compile_to_assembly(code.args[2], withargs, (end, height + 1), height + 1))
        o.extend(['DUP1', 'MLOAD', 'PUSH1', 1, 'ADD', 'DUP1', 'DUP3', 'MSTORE'])
        # stack: len(loops), index memory address, new index
        o.extend(['DUP3', 'EQ', 'ISZERO', start, 'JUMPI', end, 'JUMPDEST', 'POP', 'POP'])
        return o
    elif code.value == 'break':
        if not break_dest:
            raise Exception("Invalid break")
        dest, break_height = break_dest
        return ['POP'] * [height - break_height] + [dest, 'JUMP']
    elif code.value == 'with':
        o = []
        o.extend(compile_to_assembly(code.args[1], withargs, break_dest, height))
        old = withargs.get(code.args[0], None)
        withargs[code.args[0]] = height
        o.extend(compile_to_assembly(code.args[2], withargs, break_dest, height + 1))
        if old:
            withargs[code.args[0]] = old
        else:
            del withargs[code.args[0]]
        return o
    elif code.value == 'lll':
        o = []
        begincode = mksymbol()
        endcode = mksymbol()
        o.extend([endcode, 'JUMP', begincode, 'BLANK'])
        o.append(compile_to_assembly(code.args[0], {}, None, 0)) # Append is intentional
        o.extend([endcode, 'JUMPDEST', begincode, endcode, 'SUB', begincode])
        o.extend(compile_to_assembly(code.args[1], withargs, break_dest, height))
        o.extend(['CODECOPY', begincode, endcode, 'SUB'])
        return o
    elif code.value == 'seq':
        o = []
        for arg in code.args:
            o.extend(compile_to_assembly(arg, withargs, break_dest, height))
        return o
    elif code.value == 'clamplt':
        o = compile_to_assembly(code.args[0], withargs, break_dest, height)
        o.extend(compile_to_assembly(code.args[1], withargs, break_dest, height + 1))
        o.extend(['DUP2'])
        # Stack: num num bound
        o.extend(['LT', 'ISZERO', 'PC', 'JUMPI'])
        return o
    elif code.value == 'clamp':
        o = compile_to_assembly(code.args[0], withargs, break_dest, height)
        o.extend(compile_to_assembly(code.args[1], withargs, break_dest, height))
        o.extend(['DUP1'])
        o.extend(compile_to_assembly(code.args[2], withargs, break_dest, height + 2))
        o.extend(['SWAP1', 'GT', 'PC', 'JUMPI'])
        o.extend(['DUP1', 'SWAP2', 'LT', 'PC', 'JUMPI'])
        return o
    elif code.value == 'clamp_nonzero':
        o = compile_to_assembly(code.args[0], withargs, break_dest, height)
        o.extend(['DUP1', 'ISZERO', 'PC', 'JUMPI'])
        return o
    else:
        raise Exception("Weird code element: "+repr(code))

def assembly_to_evm(assembly):
    posmap = {}
    sub_assemblies = []
    codes = []
    pos = 0
    for i, item in enumerate(assembly):
        if is_symbol(item):
            if assembly[i + 1] == 'JUMPDEST' or assembly[i + 1] == 'BLANK':
                posmap[item] = pos # Don't increment position as the symbol itself doesn't go into code
            else:
                pos += 3 # PUSH2 highbits lowbits
        elif item == 'BLANK':
            pos += 0
        elif isinstance(item, list):
            c = assembly_to_evm(item)
            sub_assemblies.append(item)
            codes.append(c)
            pos += len(c)
        else:
            pos += 1
    o = b''
    for i, item in enumerate(assembly):
        if is_symbol(item):
            if assembly[i + 1] != 'JUMPDEST' and assembly[i + 1] != 'BLANK':
                o += bytes([PUSH_OFFSET + 2, posmap[item] // 256, posmap[item] % 256])
        elif isinstance(item, int):
            o += bytes([item])
        elif isinstance(item, str) and item.upper() in opcodes:
            o += bytes([opcodes[item.upper()][0]])
        elif item[:4] == 'PUSH':
            o += bytes([PUSH_OFFSET + int(item[4:])])
        elif item[:3] == 'DUP':
            o += bytes([DUP_OFFSET + int(item[3:])])
        elif item[:4] == 'SWAP':
            o += bytes([SWAP_OFFSET + int(item[4:])])
        elif item == 'BLANK':
            pass
        elif isinstance(item, list):
            for i in range(len(sub_assemblies)):
                if sub_assemblies[i] == item:
                    o += codes[i]
                    break
        else:
            raise Exception("Weird symbol in assembly: "+str(item))
    return o