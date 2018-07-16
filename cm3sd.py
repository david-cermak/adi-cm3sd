#!/usr/bin/python

# Copyright (c) 2014, Analog Devices, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.


# Flash download utility and simple terminal program for Cortex-M3
# based ADuCxxx devices.
#
# Author: Jim Paris <jim.paris@rigado.com>

from __future__ import print_function
import sys
import serial
import time
import struct
import re
import string
#import jimterm
import ihex

def printf(str, *args):
    print(str % args, end='')
    sys.stdout.flush()

class CM3SDError(Exception):
    pass

def int2byte(i):
    if sys.version_info < (3,):
        return chr(i)
    return bytes((i,))

def byte2int(v):
    if isinstance(v, int):
        return v
    return ord(v)

class CM3SD(object):
    def __init__(self, serial):
        self.serial = serial
        self.serial.timeout = 0.1
        self.serial.write_timeout = 1
        self.quote_re = None
        self.enter_time = None
        self.bl_msg = "bl_reps"

    def quote_raw(self, data):
        if self.quote_re is None:
            matcher = '[^%s]' % re.escape(string.printable)
            if sys.version_info < (3,):
                self.quote_re = re.compile(matcher)
                qf = lambda x: ("\\x%02x" % ord(x.group(0)))
            else:
                self.quote_re = re.compile(matcher.encode('ascii'))
                qf = lambda x: ("\\x%02x" % ord(x.group(0))).encode('ascii')
            self.quote_func = qf
        return self.quote_re.sub(self.quote_func, data).decode('ascii')

    def readuntil(self, size, eols):
        line = bytearray()
        found = False
        while True:
            c = self.serial.read(1)
            if c:
                line += c
                for eol in eols:
                    if line[-len(eol):] == eol:
                        found = True
                if found or (size is not None and len(line) >= size):
                    break
            else:
                break
        return bytes(line)

    def grep_bl_message(self):
        start = time.time()
        msg_pos = 0
        c = b''
        while (time.time() - start) <= 1:
            c = self.serial.read(1)
            if len(c) == 1: 
                #print(c)
                if self.bl_msg[msg_pos] == chr(c[0]):
                    msg_pos = msg_pos + 1
                    if msg_pos == len(self.bl_msg):
                        return True
                else:
                    msg_pos = 0
        return False
        
    def open(self):
        """Initialize communication with device"""

        printf("Hold BOOT and press RESET.\n")
        printf("Waiting for bootloader...");
        tries = 0
        while True:
            try:
                while self.serial.read(1):
                    pass
                self.serial.flushInput()
                while True:
                    self.serial.write(int2byte(0x08))
                    if self.grep_bl_message():
                        break
                response = self.readuntil(100, [b'\r\n', b'\n\r'])
                printf(response)
                if len(response) >= 21:
                    response += b'      ' # extra chars for some possibly shorter ids
                    response = response[:24]
                    ident = struct.unpack('15s3s4s2s', response)
                    # ident[0] is typically 'ADuCRF101  128 '
                    if ident[0].startswith(b'ADuC'):
                        break
                    else:
                        printf("/")
                elif len(response) > 0:
                    printf("?")
                else:
                    printf(".")
            except (OSError, SerialException) as e:
                s = str(e)
                if "returned no data" in s or "temporarily unavailable" in s:
                    printf("\n--- Error: maybe the device is open elsewhere?\n")
                raise
            tries += 1
            if tries > 5:
                # re-open the port
                tries = 0
                self.serial.close()
                self.serial.open()

        printf(" ok\nChip: '%s' revision '%s'\n",
               self.quote_raw(ident[0]),
               self.quote_raw(ident[1]))
        self.enter_time = time.time()

    def command(self, cmd, value, data = b'', timeout = 1.0, expect = 0x06):
        """Send a command to the device and return the response byte"""

        if len(cmd) != 1 or value < 0 or value > 0xffffffff:
            raise ValueError("bad cmd or value")
        if len(data) > 250:
            raise ValueError("too much data")
        out = struct.pack('>BBBBI', 0x07, 0x0e, 5 + len(data), ord(cmd), value)
        out += data
        checksum = 0
        for v in out[2:]:
            checksum += byte2int(v)
        checksum = (256 - (checksum % 256)) % 256
        out += int2byte(checksum)

        # Send command
        start = time.time()
        self.serial.write(out)

        # Wait for response
        if not self.grep_bl_message():
            raise CM3SDError("Failed to find msg start seq")
        start = time.time()
        while (time.time() - start) <= timeout:
            c = self.serial.read(1)
            if c is not None and len(c) > 0:
                break

        if expect is not None:
            if c is None:
                raise CM3SDError("Command failed: timed out")
            if c != int2byte(expect):
                raise CM3SDError("Command failed: got " + repr(c))
        return None

    def erase(self):
        printf("Bulk erase...")
        ret = self.command('E', 0x00000000, int2byte(0x00))
        printf(" ok\n")

    def reset(self):
        # If the programming was super fast, the user might not have
        # released BOOT yet, so just warn and wait before actually
        # resetting.
        if self.enter_time:
            elapsed = time.time() - self.enter_time
            if elapsed < 1:
                printf("Will reset shortly; ensure BOOT is not pressed\n")
                time.sleep(2 - elapsed)
        printf("Resetting...")
        ret = self.command('R', 0x00000001)
        printf(" ok\n")

    def write(self, hexfile):
        """Write hex file to flash"""

        ih = ihex.IHex.read(hexfile)
        size = sum(map(len, ih.areas.values()))
        if size <= 0:
            print("Skipping empty file %s" % hexfile)
            return
        minaddr = min(ih.areas.keys())
        eraseaddr = (minaddr&0xFFFFFE00)
        maxaddr = max(map(lambda x: x[0] + len(x[1]), ih.areas.items())) - 1
        maxeraseaddr = (maxaddr&0xFFFFFE00)+0x200
        printf("File: %s (%d bytes, 0x%08x-0x%08x), erase(0x%08x-0x%08x)\n",
               hexfile.name, size, minaddr, maxaddr, eraseaddr, maxeraseaddr)
        pages = b'\x01'
        while eraseaddr <  maxeraseaddr:
            printf("\rErasing... %d", eraseaddr)
            ret = self.command('E', eraseaddr, pages)
            eraseaddr = eraseaddr + 0x200

        def chunked(data, size):
            for i in range(0, len(data), size):
                yield (i, data[i:i+size])

        written = 0
        # Flash each contiguous area
        for (addr, data) in ih.areas.items():
            # Break into chunks of up to 250 bytes
            for (offset, chunk) in chunked(data, 248):
                # Write this chunk
                printf("\rFlashing... %d/%d | %x %x %x |", written, size, addr, offset, len(chunk))
                ret = self.command('W', addr + offset, chunk)
                written += len(chunk)
        printf("\rFlashing... %d/%d ok\n", written, size)

if __name__ == "__main__":
    import argparse

    formatter = argparse.ArgumentDefaultsHelpFormatter
    description = ("Flash download utility and simple terminal program for "
                   "Cortex-M3 based ADuCxxx devices.")
    parser = argparse.ArgumentParser(description = description,
                                     formatter_class = formatter)

    parser.add_argument("device", metavar="DEVICE",
                        help="Serial device")

    parser.add_argument("--baudrate", "-b", metavar="BAUD", type=int,
                        help="Baudrate", default=115200)

    group = parser.add_argument_group("Actions",
                                      ("Actions are performed "
                                       "in the order listed below."))
    group.add_argument("--write", "-w", metavar="HEX", default=[],
                       type=argparse.FileType('r'), action='append',
                       help="Hex file to write (may be repeated)")
    group.add_argument("--reset", "-r", action="store_true",
                       help="Reset and run")
    group.add_argument("--terminal", "-t", action="store_true",
                       help="Interactive terminal")

    args = parser.parse_args()

#    if args.all:
#        args.erase = True
#        args.write.append(args.all)
#        args.reset = True
#        args.terminal = True

    # Only need to use the subclassed Serial if we're opening
    # a terminal afterwards.
    if args.terminal:
#        serial = jimterm.MySerial(args.device, 9600)
        pass
    else:
        ser = serial.Serial(args.device, 115200)


    # Sometimes the baudrate seems to not get set properly; this may
    # be a Linux cdc-acm driver bug.  Changing it helps.
    # ser.setBaudrate(38400)
    # ser.setBaudrate(args.baudrate)

    cm3sd = CM3SD(ser)
    if args.write or args.erase or args.reset:
        cm3sd.open()

    # if args.erase:
    #     cm3sd.erase()

    if args.write is not None:
        for hexfile in args.write:
            cm3sd.write(hexfile)

    if args.reset:
        cm3sd.reset()

    # if args.terminal:
    #     term = jimterm.Jimterm([serial], raw = not sys.stdout.isatty())
    #     term.print_header([args.device], [args.baudrate], sys.stderr)
    #     term.run()
