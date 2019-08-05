from turas import Assemble
from ELF import *
from struct import unpack, pack
from functools import reduce


class Cubin():
  def __init__(self, arch=70):
    self.header = Header()
    self.programs = []
    self.shstrtab = Section()
    self.strtab   = Section()
    self.symtab   = Section()

    self.sections = []

    # Symbol list?
    # (text, shared, constant0){n} {name}
    self.kern_syms = []
    self.name_syms = []

    self.sec_idx_dict = {} # name : sec_idx
    self.sym_idx_dict = {} # name : sym_idx
    self.sec_idx = 0
    self.sym_idx = 0
    # Add null section and null symbol at the begining
    self.sections.append(Section())
    self.sec_idx += 1

    self.kern_syms.append(Symbol())
    self.sym_idx += 1

    self.arch = arch
    self.Init(arch)

  def Init(self, arch):
    '''
    Header information.
    Set flags/info for shstrtab/strtab/symtab.
    Init all programs.
    '''
    # Update header information.
    self.header.phnum = 3
    self.header.flags |= (arch << 16) + arch

    # Setup sections.
    self.shstrtab.name    = b'.shstrtab'
    self.shstrtab.sh_type = 3 # SHT_STRTAB
    self.sections.append(self.shstrtab)
    self.sec_idx_dict[b'.shstrtab'] = self.sec_idx
    self.sec_idx += 1

    self.strtab.name      = b'.strtab'
    self.strtab.sh_type = 3
    self.sections.append(self.strtab)
    self.sec_idx_dict[b'.strtab'] = self.sec_idx
    self.sec_idx += 1

    self.symtab.name       = b'.symtab'
    self.symtab.sh_type    = 2 # SHT_SYMTAB
    self.symtab.sh_entsize = Symbol.ENTRY_SIZE # 24
    self.symtab.sh_link    = 2 # TODO: Make sure it's strtab.
    self.sections.append(self.symtab)
    self.symtab.sh_align   = 8 
    self.sec_idx_dict[b'.symtab'] = self.sec_idx
    self.sec_idx += 1


    # Init programs.
    self.p_hdr      = Program(6, 5) # (type, flags)
    self.p_hdr.filesz = 0xa8
    self.p_hdr.memsz  = 0xa8 
    self.p_progbits = Program(1, 5)
    self.p_nobits   = Program(1, 6)
    self.programs.extend([self.p_hdr, self.p_progbits, self.p_nobits])

  def GenerateNvInfo(self, section, name):
    data = b''
    # Entry size: 12. (bbhll) (BB, 2B, 4B, 4B)
    # EIATTR_MAX_STACK_SIZE (0x0423)
    kernel_symtab_idx = self.sym_idx_dict[name]  # TODO: Why?
    MAX_STACK_SIZE = 0
    data += pack('<bbhll', 0x4, 0x23, 0x8, kernel_symtab_idx, MAX_STACK_SIZE)

    # EIATTR_MIN_STACK_SIZE (0x0412)
    MIN_STACK_SIZE = 0
    data += pack('<bbhll', 0x4, 0x12, 0x8, kernel_symtab_idx, MAX_STACK_SIZE)

    # EIATTR_FRAME_SIZE (0x0411)
    FRAME_SIZE = 0
    data += pack('<bbhll', 0x4, 0x11, 0x8, kernel_symtab_idx, FRAME_SIZE)
    
    # Update section 
    section.data = data

    # TODO: Update header information.
    section.name = b'.nv.info'
    section.sh_size = len(data)
    section.sh_type = 0x70000000
    section.sh_link = self.sec_idx_dict[b'.symtab']
    section.sh_align = 4

  def GenerateNvInfoName(self, kernel, section, name, params):
    '''
    params = [sizeof each param]
    '''
    data = b''
    size_params = reduce(lambda x,y : x+y, params)
    # EIATTR_PARAM_CBANK (0x040a)
    kernel_symtab_idx = self.sym_idx_dict[name]
    data += pack('<bbhlhh', 0x4, 0x0a, 0x8, kernel_symtab_idx, 0x160, size_params)

    # EIATTR_CBANK_PARAM_SIZE (0x0319)
    data += pack('<bbH', 0x3, 0x19, size_params)

    # for each parameter:
    # EIATTR_KPARAM_INFO (0x0417); size: 0xc
    param_offset = size_params
    for ordinal, param in reversed(list(enumerate(params))):
      param_offset -= param
      param_flag = ((param // 4) << 20) + 0x1f000 + 0x000 # space (4bits) + logAlign (8bits); always 0
      data += pack('<bbHIHHI', 0x04, 0x17, 0xc, 0x0, ordinal, param_offset, param_flag) # Index: always 0 (4B)

    # EIATTR_MAXREG_COUNT (0x031b)
    data += pack('<bbH', 0x03, 0x1b, 0xff) # MAXREG_COUNT=0xff

    # EIATTR_EXIT_INSTR_OFFSETS (0x041c)
    size = len(kernel['ExitOffset']) * 4
    data += pack('<bbH', 0x04, 0x1c, size)
    # Maybe more than one exit.
    for exit_offset in kernel['ExitOffset']:
      data += pack('<I', exit_offset)

    section.data = data
    section.sh_size = len(data)
    section.name = b'.nv.info.' + name
    # TODO: Update flags
    section.sh_type = 0x70000000
    section.sh_link = self.sec_idx_dict[b'.symtab']
    section.sh_info = self.sec_idx_dict[b'.text.'+name]
    section.sh_align = 4


  def GenerateNvConst(self, kernel, section, name, params):
    # FIXME: size = 0x160 + sizeof(params)
    size = 0x160 + reduce(lambda x,y:x+y, params)
    data = b'\x00' * size # Not sure why all kernels have this section.

    section.data     = data
    section.sh_size  = size 
    section.name     = b'.nv.constant0.' + name
    section.sh_flags = 2 # SHF_ALLOC
    section.sh_type  = 1 # PROGBITS
    section.sh_info = self.sec_idx_dict[b'.text.'+name]
    section.sh_align = 4
    
  def GenerateText(self, kernel, section, name):
    data = b''
    for code in kernel['KernelData']:
      data += pack('<QQ', (code >> 64) & 0xffffffffffffffff, 
                          (code)       & 0xffffffffffffffff)

    section.data = data

    # Other flags
    section.name     = b'.text.' + name
    section.sh_type  = 1 # PROGBITS
    section.sh_flags = 6 + (kernel['BarCnt'] << 20)
    section.sh_size  = len(data)
    section.sh_link = self.sec_idx_dict[b'.symtab']
    section.sh_info  = 4 + (kernel['RegCnt'] << 24) # RegCnt
    section.sh_align = 128
    

  def UpdateShstrtab(self):
    shstr = b''
    shstr_idx = 0
    for sec in self.sections:
      sec.sh_name = shstr_idx
      shstr += sec.name
      shstr += b'\x00'
      shstr_idx += len(sec.name) + 1
    self.shstrtab.data = shstr
    self.shstrtab.sh_size = shstr_idx

  def UpdateStrtab(self):
    strtab = b''
    strtab_idx = 0
    for sym in self.kern_syms:
      sym.st_name = strtab_idx
      strtab += sym.name
      strtab += b'\x00'
      strtab_idx += len(sym.name) + 1
    for sym in self.name_syms:
      sym.st_name = strtab_idx
      strtab += sym.name
      strtab += b'\x00'
      strtab_idx += len(sym.name) + 1
    self.strtab.data = strtab
    self.strtab.sh_size = strtab_idx

  def UpdateOffset(self):
    '''
    1. sh_offset
    2. start of section headers
    3. start of program headers
    4. header idx of shstrtab
    '''
    current_offset = 0
    current_offset += Header.HEADER_SIZE
    for sec in self.sections:
      sec.sh_offset = current_offset
      current_offset += sec.sh_size
    self.header.shoff = current_offset
    self.header.shnum = len(self.sections)
    current_offset += Section.HEADER_SIZE * len(self.sections)
    self.header.phoff = current_offset
    self.header.phnum = len(self.programs)
    current_offset += Program.PHDR_SIZE * len(self.programs)

    self.header.shstrndx = self.sec_idx_dict[b'.shstrtab']


  # TODO: name of the kernel?
  def AddKernel(self, kernel, name, params):
    # Only support *ONE* kernel per cubin file.
    '''
    For each kernel:
      1. Create sections and update index
      2. Create symbols and update index
      3. Add .text.{name}
      4. Add .nv.shared.{name} (optional)
      5. Add entries in symbol table
      6. Add 3 entry to .nv.info.
    '''
    #####################################
    # Add sections (record section index)
    #####################################
    _nv_info         = Section()
    self.sections.append(_nv_info)
    self.sec_idx_dict[b'.nv.info'] = self.sec_idx
    self.sec_idx += 1

    _nv_info_kernel  = Section()
    self.sections.append(_nv_info_kernel)
    self.sec_idx_dict[b'.nv.info.'+name] = self.sec_idx
    self.sec_idx += 1

    _nv_const_kernel = Section()
    self.sections.append(_nv_const_kernel)
    self.sec_idx_dict[b'.nv.constant0.'+name] = self.sec_idx
    self.sec_idx += 1

    _text_kernel     = Section()
    self.sections.append(_text_kernel)
    self.sec_idx_dict[b'.text.'+name] = self.sec_idx
    self.sec_idx += 1

    if kernel['SmemSize'] > 0:
      _nv_smem_kernel = Section()
      self.sec_idx_dict[b'.nv.shared.'+name] = self.sec_idx
      self.sec_idx += 1 

    ###################
    # Add symbol entry.
    ###################
    text_sym_entry = Symbol()
    text_sym_entry.name    = b'.text.' + name
    text_sym_entry.st_info = 3 # Bind local
    text_sym_entry.st_shndx = self.sec_idx_dict[b'.text.' + name]
    self.kern_syms.append(text_sym_entry)
    self.sym_idx_dict[b'.text.' + name] = self.sym_idx
    self.sym_idx += 1

    if kernel['SmemSize'] > 0:
      smem_sym_entry = Symbol()
      smem_sym_entry.name    = b'.nv.shared.' + name
      smem_sym_entry.st_info = 3
      smem_sym_entry.st_shndx = self.sec_idx_dict[b'.nv.shared.' + name]
      self.kern_syms.append(smem_sym_entry)
      self.sym_idx_dict[b'.nv.shared.' + name] = self.sym_idx
      self.sym_idx += 1
    
    const_sym_entry = Symbol()
    const_sym_entry.name    = b'.nv.constant0.' + name
    const_sym_entry.st_info = 3
    const_sym_entry.st_shndx = self.sec_idx_dict[b'.nv.constant0.' + name]
    self.kern_syms.append(const_sym_entry)
    self.sym_idx_dict[b'.nv.constant0.' + name] = self.sym_idx
    self.sym_idx += 1

    # Add name symbol
    name_sym_entry = Symbol()
    name_sym_entry.name     = name
    name_sym_entry.st_info  = 0x12 # FUNC
    name_sym_entry.st_other = 0x10 
    name_sym_entry.st_size  = len(kernel['KernelData'] * 16)
    name_sym_entry.st_shndx = self.sec_idx_dict[b'.text.' + name]
    self.name_syms.append(name_sym_entry)
    self.sym_idx_dict[name] = self.sym_idx
    self.sym_idx += 1

    ###############################
    # Generate section data (flags)
    ###############################
    # Add .nv.info
    self.GenerateNvInfo(_nv_info, name)
    # Add .nv.info.name
    self.GenerateNvInfoName(kernel, _nv_info_kernel, name, params)
    # Add .nv.constant0.name
    self.GenerateNvConst(kernel, _nv_const_kernel, name, params)
    # Add .text.name
    self.GenerateText(kernel, _text_kernel, name)
    # Add .nv.shared.name
    if kernel['SmemSize'] > 0:
      pass

    ########################
    # Update shstrtab/strtab
    ########################
    self.UpdateShstrtab()
    self.UpdateStrtab()

    ###############
    # Update symtab
    ###############
    for sym in self.kern_syms:
      self.symtab.data += sym.PackEntry()
    for sym in self.name_syms:
      sym.st_size = self.sections[sym.st_shndx].sh_size
      self.symtab.data += sym.PackEntry()
    self.symtab.sh_size = len(self.symtab.data)
    self.symtab.sh_info = self.sec_idx_dict[b'.strtab'] + 1


    #######################
    # Update offset
    #######################
    self.UpdateOffset()
    # Update program offset
    self.p_hdr.offset = self.header.phoff
    self.p_progbits.offset = self.sections[self.sec_idx_dict[b'.nv.constant0.'+name]].sh_offset
    self.p_progbits.filesz = self.sections[self.sec_idx_dict[b'.nv.constant0.'+name]].sh_size + \
      self.sections[self.sec_idx_dict[b'.text.'+name]].sh_size
    self.p_progbits.memsz  = self.p_progbits.filesz


  def Write(self, path):
    '''
    Write data to file.
    Order: 
       1. Header.
       2. shstrtab, strtab, symtab, .nv.info.
       3. info_secs, const_secs, text_secs, smem_secs
       4. shdrs.
       5. phdrs.
    '''
    with open(path, 'wb') as file:
      file.write(self.header.PackHeader())
      for sec in self.sections:
        file.write(sec.data)
      for sec in self.sections:
        file.write(sec.PackHeader())
      for pro in self.programs:
        file.write(pro.PackHeader())