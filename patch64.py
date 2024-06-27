from pwn import *
from subprocess import getoutput
import sys, os


class patch64_handler:
    def __init__(self, filename, sandboxfile, debugFlag):
        context.arch = 'amd64'
        self.filename = filename
        self.ct = self.make_sandbox(sandboxfile)
        self.elf = ELF(filename)
        self.debugFlag = debugFlag

    def pr(self, a, addr):
        log.success(a + '===>' + hex(addr))

    def run(self):
        if self.debugFlag == 0:
            sys.stdout = open(os.devnull, 'w')
        if self.elf.pie == True:
            self.patch_pie_elf()
        else:
            self.patch_nopie_elf()
        sys.stdout = sys.__stdout__
        self.elf.save(self.filename + '.patch')
        os.system('chmod +x ' + self.filename + '.patch')
        log.success('input file: ' + self.filename)
        log.success('output file: ' + self.filename + '.patch')
        print('Patch file successfully!!!')

    def run_partial(self):
        if self.debugFlag == 0:
            sys.stdout = open(os.devnull, 'w')
        inject_code = asm('endbr64')
        inject_code += asm('push rbp')
        inject_code += self.inject_code_build() + 3 * asm('nop')
        print('============================inject code into eh_frame_hdr============================')
        print(disasm(inject_code))
        print('eh_frame_hdr.sh_size===>' + str(hex(self.elf.get_section_by_name('.eh_frame_hdr').header.sh_size)))
        print('inject_code.length===>' + str(hex(len(inject_code))))
        eh_frame_addr = self.elf.get_section_by_name('.eh_frame_hdr').header.sh_addr
        self.elf.write(eh_frame_addr, inject_code)
        self.edit_program_table_header()
        sys.stdout = sys.__stdout__

        self.elf.save(self.filename + '.patch')
        os.system('chmod +x ' + self.filename + '.patch')
        log.success('input file: ' + self.filename)
        log.success('output file: ' + self.filename + '.patch')
        print('Patch file successfully!!!')

    def make_sandbox(self, sandboxfile):
        sandboxCt = eval(getoutput('seccomp-tools asm ' + sandboxfile + ' -a amd64 -f inspect'))
        os.system('seccomp-tools asm ' + sandboxfile + ' -a amd64 -f raw | seccomp-tools disasm -')
        ct = []
        for i in range(len(sandboxCt) // 8):
            ct.append(u64(sandboxCt[i * 8:i * 8 + 8]))
        ct.reverse()
        return ct

    def inject_code_build(self):
        inject_code = asm(shellcraft.amd64.prctl(38, 1, 0, 0, 0))
        for i in self.ct:
            if i > 0x3fffffff:
                a = 'mov rax,' + str(i)
                inject_code += asm(a)
                inject_code += asm('push rax')
            else:
                a = 'push ' + str(i)
                inject_code += asm(a)
        inject_code += asm(shellcraft.amd64.push('rsp'))
        inject_code += asm(shellcraft.amd64.push(len(self.ct)))
        inject_code += asm('mov r10,rcx')
        inject_code += asm(shellcraft.amd64.prctl(0x16, 2, 'rsp'))
        tmp = len(self.ct) * 8 + 0x10
        inject_code += asm('add rsp,' + str(hex(tmp + 0x10)))
        return inject_code

    def edit_program_table_header(self):
        program_table_header_start = self.elf.address + self.elf.header.e_phoff
        num_of_program_table_header = self.elf.header.e_phnum
        size_of_program_headers = self.elf.header.e_phentsize
        if self.debugFlag != 0:
            self.pr('program_table_header_start', program_table_header_start)
            self.pr('num_of_program_table_header', num_of_program_table_header)
            self.pr('size_of_program_headers', size_of_program_headers)

        for i in range(num_of_program_table_header):
            p_type = self.elf.get_segment(i).header.p_type
            p_flags = self.elf.get_segment(i).header.p_flags
            if p_type == 'PT_GNU_EH_FRAME' and p_flags == 4:
                self.elf.write(program_table_header_start + i * size_of_program_headers + 4, p32(5))
                print('edit program_table_element[' + str(i) + '].p_flags===>r_x')
                self.elf.write(program_table_header_start + i * size_of_program_headers, p32(1))
                print('edit program_table_element[' + str(i) + '].p_type===>LOAD')

    def patch_pie_elf(self):
        eh_frame_addr = self.elf.get_section_by_name('.eh_frame_hdr').header.sh_addr
        start_offset = self.elf.header.e_entry
        offset = self.elf.read(start_offset, 0x40).find(b'\x00\xff\x15') - 6  # lea rdi,?
        offset1 = u32(self.elf.read(start_offset + offset + 3, 4))
        if offset1 > 0x80000000:
            offset1 -= 0x100000000
        main_addr = start_offset + offset + offset1 + 7
        self.pr('eh_frame_addr', eh_frame_addr)
        self.pr('start_offset', start_offset)
        self.pr('main_addr', main_addr)
        print('=================================edit _start==================================')
        print(
            'replace _start+' + str(offset) + '------>change __libc_start_main\'s first parameter: main->eh_frame_hdr')
        print(disasm(self.elf.read(start_offset + offset, 7)))
        s = 'lea rdi,[rip+' + str(hex(eh_frame_addr - (start_offset + offset) - 7)) + '];'
        print('                ||               ')
        print('                ||               ')
        print('                \/               ')
        print(disasm(asm(s)))

        inject_code = asm(
            "push rax;push rbx;push rcx;push rdx;push rdi;push rsi;push r8;push r9;push r10; push r11;push r13;push r14;push r15")
        inject_code += self.inject_code_build()
        tail = 'lea r12,[rip' + str(hex(main_addr - (eh_frame_addr + len(
            inject_code)) - 7)) + '];pop rax;pop rbx;pop rcx;pop rdx;pop rdi;pop rsi;pop r8;pop r9;pop r10; pop r11;pop r13;pop r14;pop r15;jmp r12;'

        inject_code += asm(tail)
        print('============================inject code into eh_frame_hdr========================')
        print(disasm(inject_code))
        print('eh_frame_hdr.sh_size===>' + str(hex(self.elf.get_section_by_name('.eh_frame_hdr').header.sh_size)))
        print('inject_code.length===>' + str(hex(len(inject_code))))
        self.elf.write(start_offset + offset, asm(s))
        self.elf.write(eh_frame_addr, inject_code)
        self.edit_program_table_header()

    def patch_nopie_elf(self):

        program_base = self.elf.address
        self.pr('program_base', program_base)

        eh_frame_addr = self.elf.get_section_by_name('.eh_frame_hdr').header.sh_addr
        start_offset = self.elf.header.e_entry
        offset = self.elf.read(start_offset, 0x40).find(b'\x00\xff\x15') - 6  # mov rdi,?
        main_addr = u32(self.elf.read(start_offset + offset + 3, 4))
        self.pr('eh_frame_addr', eh_frame_addr)
        self.pr('start_offset', start_offset)
        self.pr('main_addr', main_addr)
        print('=================================edit _start==================================')
        print(
            'replace _start+' + str(offset) + '------>change __libc_start_main\'s first parameter: main->eh_frame_hdr')
        print(disasm(self.elf.read(start_offset + offset, 7)))
        s = 'mov rdi,' + str(eh_frame_addr) + ';'
        print('                ||               ')
        print('                ||               ')
        print('                \/               ')
        print(disasm(asm(s)))
        inject_code = asm(
            "push rax;push rbx;push rcx;push rdx;push rdi;push rsi;push r8;push r9;push r10; push r11;push r13;push r14;push r15")
        inject_code += self.inject_code_build()
        tail = 'mov r12,' + str(
            main_addr) + '; pop rax;pop rbx;pop rcx;pop rdx;pop rdi;pop rsi;pop r8;pop r9;pop r10; pop r11;pop r13;pop r14;pop r15;jmp r12;'
        inject_code += asm(tail)
        print('============================inject code into eh_frame_hdr============================')
        print(disasm(inject_code))
        print('eh_frame_hdr.sh_size===>' + str(hex(self.elf.get_section_by_name('.eh_frame_hdr').header.sh_size)))
        print('inject_code.length===>' + str(hex(len(inject_code))))
        self.elf.write(start_offset + offset, asm(s))
        self.elf.write(eh_frame_addr, inject_code)
        self.edit_program_table_header()


def main():
    filename = sys.argv[1]
    sandboxfile = sys.argv[2]
    debugFlag = 0
    try:
        tmp = sys.argv[3]
        debugFlag = 1
    except IndexError:
        pass
    patch64_handler(filename, sandboxfile, debugFlag).run_partial()


if __name__ == '__main__':
    main()
