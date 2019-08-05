from grammar import ProcessAsmLine, grammar, GenCode, ctrl_re, pred_re
from itertools import accumulate
import re

def StripSpace(file):
  # Replace all commands, space, tab with ''
  file = re.sub(r'\n\n', r'\n', file)
  file = re.sub(r'#.*', '', file)
  # Tailing space.
  file = re.sub(r'(?<=;).*', '', file)
  return file

def Assemble(file, include=None):
  '''
  return {
      RegCnt       => $regCnt,
      BarCnt       => $barCnt,
      ExitOffsets  => \@exitOffsets,
      CTAIDOffsets => \@ctaidOffsets,
      CTAIDZUsed   => $ctaidzUsed,
      KernelData   => \@codes,
  }
  '''
  # After preprocess.
  # for each line in the file.
  # 1. ProcessAsmLine
  #    Parse line to get: {ctrl}, {pred}, {op}, reset
  # 2. Apply register mapping.
  # 3. Parse op(flags) & operands?
  #    Need to write capture rules for instructions(oprands, flags)
  # 4. Generate binary code.
  #    Op | Flags | Operands
  file = StripSpace(file)
  num_registers = 8
  num_barriers  = 0
  smem_size     = 0
  const_size    = 0
  exit_offsets   = []
  labels = {} # Name => line_num
  branches = [] # Keep track of branch instructions (BRA)
  line_num = 0

  instructions = []
  for file_line_num, line in enumerate(file.split('\n')): # TODO: 
    if line == '':
      continue
    line_result = ProcessAsmLine(line, line_num)
    if(line_result):
      # Push instruction data to list
      instructions.append(line_result)
      if line_result['op'] == 'BRA':
        branches.append(line_result)
      if line_result['op'] == 'EXIT':
        exit_offsets.append(line_num * 16)
      line_num += 1
      continue # Ugly control flow
    label_result = re.match(r'(^[a-zA-Z]\w*):', line)
    # TODO: Move this to preprocess.
    if label_result:
      # Match a label
      labels[label_result.group(1)] = line_num
    else:
      print(line)
      raise Exception(f'Cannot recogonize {line} at line{file_line_num}.\n')

  # Append the tail BRA.
  instructions.append(ProcessAsmLine('--:-:-:Y:0  BRA -0x10;', len(instructions)+1))

  # Append NOPs to satisfy 128-bytes align.
  while len(instructions) % 8 != 0:
    # Pad NOP.
    instructions.append(ProcessAsmLine('--:-:-:Y:0  NOP;', len(instructions)+1))

  # Remap labels
  for bra_instr in branches:
    label = re.sub(r'^\s*', '', bra_instr['rest'])
    label = label.split(';')[0]
    relative_offset = (labels[label] - bra_instr['line_num'] - 1) * 0x10 
    bra_instr['rest'] = ' ' + hex(relative_offset) + ';'

  # Parse instructions.
  # Generate binary code. And insert to the instructions list.
  codes = []
  for instr in instructions:
    # Op, instr(rest part), 
    op = instr['op']
    rest = instr['rest']
    grams = grammar[op]
    # If match the rule of that instruction.
    for gram in grams:
      result = re.match(gram['rule'], op + rest)
      if result == None:
        continue
      else:
        c_gram = gram # Current grammar. Better name?
        break
    if result == None:
      print(repr(gram))
      raise Exception(f'Cannot recognize instruction {op+rest}')

    # Update register count
    for reg in ['rd', 'rs0', 'rs1', 'rs2']:
      if reg not in result.groupdict():
        continue
      reg_data = result.groupdict()[reg]
      if reg_data == None or reg_data == 'RZ':
        continue
      else:
        reg_idx = int(reg_data[1:])
        if reg_idx + 1 > num_registers:
          num_registers = reg_idx + 1
    
    # Update barrier count.
    if op == 'BAR':
      barrier_idx = int(result.groupdict()['ibar'], 0)
      if barrier_idx >= 0xf:
        # TODO: Add line number here.
        raise Exception(f'Barrier index must be smaller than 15. {barrier_idx} found.')
      if barrier_idx + 1 > num_barriers:
        num_barriers = barrier_idx + 1


    code = GenCode(op, c_gram, result.groupdict(), instr)

    codes.append(code)

  # TODO: For some reasons, we need larger register count.
  if num_registers > 8:
    num_registers += 2


  return {
    # RegCnt
    'RegCnt'   : num_registers,
    # BarCnt
    'BarCnt'   : num_barriers,
    'SmemSize' : smem_size,
    'ConstSize': const_size,
    # ExitOffset
    'ExitOffset' : exit_offsets,
    # CTAIDOffset
    'KernelData' : codes
  }
    
register_map_re = re.compile(r'^[\t ]*<REGS>(.*?)\s*</REGS>\n?', re.S | re.M)
parameter_map_re = re.compile(r'^[\t ]*<PARAMS>(.*?)^\s*</PARAMS>\n?', re.S | re.M)
def SetRegisterMap(file):
  # TODO: To keep track of vector registers.
  reg_map = {}
  regmap_result = register_map_re.findall(file)
  for match_item in regmap_result:
    for line_num, line in enumerate(match_item.split('\n')):
      # Replace commands
      line = re.sub(r'#.*', '', line)
      # Replace  space
      line = re.sub(r'\s*', '', line)
      # Skip empty line
      if line == '':
        continue
      
      # reg_idx and reg_names
      reg_idx, reg_names = line.split(':')
      reg_idx = reg_idx.split(',')
      reg_names = reg_names.split(',')
      if len(reg_idx) != len(reg_names):
        raise Exception('Number of registers != number of register names.\n') # TODO: track line number.
      for i, name in enumerate(reg_names):
        if name in reg_map:
          raise Exception(f'Register name {name} already defined at line {line_num+1}.\n')
        if not re.match(r'\w+', name):
          raise Exception(f'Invalid register name {name}, at line {line_num+1}.\n')
        reg_map[name] = reg_idx[i]

  # Replace <REGISTER_MAPPING> with ''
  file = register_map_re.sub('', file)

  return file, reg_map

def SetParameterMap(file):
  '''
  <PARAMS>
  input,  8
  output, 8
  </PARAMS>
  '''
  name_list = []
  size_list = []
  # Cannot use dict. Order information is needed.
  param_dict = {'name_list' : name_list, 'size_list' : size_list}
  parammap_result = parameter_map_re.findall(file)
  for match_item in parammap_result:
    for line_num, line in enumerate(match_item.split('\n')):
      # Replace commands and space
      line = re.sub(r'#.*', '', line)
      line = re.sub(r'\s*', '', line)
      if line == '':
        continue
      name, size = line.split(',')
      if name in name_list:
        raise Exception(f'Parameter name {name} already defined.\n')
      if not re.match(r'\w+', name):
        raise Exception(f'Invalid parameter name {name}, at line {line_num+1}.\n')
      size = int(size)
      if size % 4 != 0:
        raise Exception(f'Size of parameter {name} is not a multiplication of 4. Not supported.\n')
      name_list.append(name)
      size_list.append(size)
  
  # Delete parameter text.
  file = parameter_map_re.sub('', file)

  return file, param_dict

def GetParameterConstant(param_name, params, para_offset=0):
  base = 0x160 # TODO: Better to be a global variable. (Cubin also needs this.)
  index = params['name_list'].index(param_name) # Use .index() is safe here. Elements are unique.
  prefix_sum = list(accumulate(params['size_list']))
  size = params['size_list'][index]
  offset = prefix_sum[index] - size + para_offset * 4# :)
  if size - para_offset*4 < 0:
    raise Exception(f'Parameter {param_name} is of size {size}. Cannot have offset {para_offset}.')
  return 'c[0x0][' + '0x%0.3X' % (base+offset) + ']'

# Replace register and parameter.
def ReplaceRegParamMap(file, reg_map, param_dict):
  for key in reg_map.keys():
    if key in param_dict['name_list']:
      raise Exception(f'Name {key} defined both in register and parameters.\n')
  var_re = re.compile(fr'(?<!(?:\.))\b([a-zA-Z_]\w*)(?:\[(\d)\]|\b)(?!\[0x)')
  def RepalceVar(match, regs, params):
    var = match.group(1)
    offset = match.group(2)
    if var in grammar:
      return var
    if var in reg_map:
      return 'R' + str(reg_map[var])
    if var in params['name_list']:
      if offset == None:
        return GetParameterConstant(var, params)
      else:
        offset = int(offset)
        return GetParameterConstant(var, params, offset)
    else:
      # TODO: Or not to allow use RX in the code and raise exeception here.
      return var # In case of R0-R255, RZ, PR
  # Match rest first.
  file = var_re.sub(lambda match : RepalceVar(match, reg_map, param_dict), file)

  return file
    
code_re = re.compile(r"^[\t ]*<CODE>(.*?)^\s*<\/CODE>\n?", re.MULTILINE|re.DOTALL)
def ExpandCode(file, include=None): # TODO: Better way to do this.
  # Execute include files.
  if include != None:
    for include_file in include:
      with open(include_file, 'r') as f:
        source = f.read()
        exec(source, globals())
  # Execute <CODE> block.
  def ReplaceCode(matchobj):
    exec(matchobj.group(1), globals())
    return out_
  return code_re.sub(ReplaceCode, file)

inline_re = re.compile(r'{(.*)?}', re.M)
def ExpandInline(file, include=None):
    # Execute include files.
  if include != None:
    for include_file in include:
      with open(include_file, 'r') as f:
        source = f.read()
        exec(source, globals())
  def ReplaceCode(matchobj):
    return str(eval(matchobj.group(1), globals()))
  return inline_re.sub(ReplaceCode, file)


  

if __name__ == '__main__':
  input_str = '''--:-:-:-:2    MOV R0, c[0x0][0x160];
--:-:-:-:2    MOV R1, c[0x0][0x164];
--:-:-:-:2    MOV R2, c[0x0][0x168];
--:-:-:-:5    MOV R3, c[0x0][0x16c];
--:-:-:-:2    STG.E.SYS [R0], R0;
--:-:-:-:2    STG.E.SYS [R0+4], R1;
--:-:-:-:2    STG.E.SYS [R2], R2;
--:-:-:-:2    STG.E.SYS [R2+4], R3;
--:-:-:-:2    EXIT;'''
  ReplaceRegParamMap(input_str)
