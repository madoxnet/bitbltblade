#!/usr/bin/env python

"""
BitBltBlade control script

ChangeLog:
2013-05-19:
- Add in functions for digital IO using FT2232D
- (This changelog entry is in 2013-11-26)

2012-07-23:
- Renamed from "Loightscythe" to "BitBltBlade"
- Fixed RGB convert bug
- Changed form handling code
- Added pixel level changing in colour mode
- Clear pixels after playing in LS mode

$Revision: $
$Date: $
"""

########################################################################

#Webserver imports
import cgi
import json
import logging
import logging.config
import threading
import time

import BaseHTTPServer
import SocketServer

#Expander board imports
import struct
import usb1

#somfy imports
import serial

#Python Imaging
import Image

class BBBControl():
  """
  BitBltBlade Control Class
  This handles the hardware side of things including the FTDI MPSSE for
  communications with the WS2801 LED strips as well as the Somfy Remote.
  """

  def __init__(self):
    """
    Initialise the FTDI 2232L (D) on Kean's expander board for MPSSE
    communications at 3Mhz with GPIOL0-3 as inputs and prepare data for
    feedback to webserver.
    """
    #Initialize the FTDI
    self.init_ftdi_mpsse()
    
    #Init the somfy
    self.init_somfy()
    
    #Initialise the GUI interface bits
    self.status_updated = threading.Condition()
    self.status_id = 1 #This needs to be different to the JS side (0)
    self.status = ""
    
    #Initialise the image outputting bits
    self.column_index = 0
    self.column_interval = 0.05
    self.image_loaded = False
    self.max_height = 95 #Change this depending on size of strip

    self.r_idx = 2 #My WS2801 strip  has R & B swapped, R is normally 0
    self.g_idx = 1
    self.b_idx = 0 #My WS2801 strip  has R & B swapped, B is normally 2
    
    black_pixel = "\x00\x00\x00"
    self.black_column = self.max_height * black_pixel
    self.column_buffer = self.max_height * [black_pixel]
    
    self.somfy_reset = struct.pack("B13s", 13, "\x00")
    self.somfy_getstatus = struct.pack("B2sBB9s", 13, "", 0x01, 0x02, "")
    
  def init_somfy(self):
    #Configure the somfy Serial Ports
    self.ser            = serial.Serial()
    self.ser.port       = "/dev/ttyUSB0" #This is the default for somfy
    self.ser.baudrate   = 38400
    self.ser.bytesize   = 8
    self.ser.parity     = serial.PARITY_NONE
    self.ser.stopbits   = 1
    self.ser.xonxoff    = False
    self.ser.rtscts     = False
    
    self.somfy_present = True
    try:
      self.ser.open()
      self.ser.flushInput()
      self.ser.flushOutput()
      self.ser.write(self.somfy_reset)
      time.sleep(1)
      self.ser.flushInput()
      buffer = ser.read(2048)
      ser.write(self.somfy_getstatus)
      buffer = ser.read(2048)
    except:
      self.somfy_present = False
      
  def somfy_string(self, unit_id, unit_code, command):
    return struct.pack(">5BH6B",
                        0x0C,   #Packet Length
                        0x1A, #Packet Type
                        0x00, #Sub-Type
                        0x00, #Sequence Number
                        0x00, #ID Dummy
                        unit_id, #unit_id as 16bit int
                        unit_code, #Unit Code
                        command, #Command
                        0x00, #Reserved 1
                        0x00, #Reserved 2
                        0x00, #Reserved 3
                        0x00 #RSSI N/A
                      )

  def somfy_command(self, unit, unit_code, command):
    #ser.write(somfy_string(MADOX_UNIT_ID, 3, CMD_DOWN))
    self.ser.write(self.somfy_string(unit, unit_code, command))
    self.update_status("Info: Blind command (%d,%d,%d)" % (unit, unit_code, command))
   
  def init_ftdi_mpsse(self):
    """
    FTDI MPSSE Initialization
    """
    #The following parameters are for Kean's WR703N Expander
    BITMODE_RESET = 0x00
    BITMODE_MPSSE = 0x02
    VID = 0x0403
    PID = 0x6010
    FTDI_DEVICE_OUT_REQTYPE = 0x40 #(0x02 << 5) | 0x00 | 0x00
    SIO_SET_BITMODE_REQUEST = 0x0B
    
    self.INTERFACE = 0     #INTERFACE_A 
    self.OUT_EP    = 0x02  #INTERFACE_A OUT_EP
    self.IN_EP     = 0x81  #INTERFACE_A IN_EP 
    
    context = usb1.LibUSBContext()
    #Open the FTDI
    self.ftdi = context.openByVendorIDAndProductID(VID, PID)
    if self.ftdi == None:
      #Failed to connect to the FTDI
      self.connected = False
    else:
      self.connected = True
      #Check if there the kernel driver is attached
      if self.ftdi.kernelDriverActive(self.INTERFACE) == True:
        #If so, ask for it to be detached
        self.ftdi.detachKernelDriver(self.INTERFACE)
      #Claim this interface
      self.ftdi.claimInterface(self.INTERFACE)
      #Set MPSSE Mode
      self.ftdi.controlWrite(FTDI_DEVICE_OUT_REQTYPE,
                         SIO_SET_BITMODE_REQUEST,
                         SIO_SET_BITMODE_REQUEST | (BITMODE_MPSSE << 8),
                         self.INTERFACE,
                         0)
      #Set Clock
      clock_cmd = "\x86\x00\x01" #3Mhz (Ref AN_108 3.8.1)
      self.ftdi.bulkWrite(self.OUT_EP, clock_cmd)
    
  def init_pins(self):
    """
    Interface A Pin Initialization
    """
    #    GPIOL3, GPIOL2, GPIOL1, GPIOL0, TMS/CS, TDO/DI, TDI/DO, TCK/SK
    #DIR In    , In    , In    , In    , Out   , In    , Out   , Out
    #Val 0     , 0     , 0     , 0     , 0     , 0     , 0     , 0
    if self.connected == True:
      self.set_lo_byte(0x00,0x0B)
      #gpio_cmd = "\x80\x00\x0B"

  def get_lo_byte(self):
    if self.connected == True:
      gpio_cmd = "\x81"
      self.ftdi.bulkWrite(self.OUT_EP, gpio_cmd)
      return self.ftdi.bulkRead(self.IN_EP, 1, timeout=500)

  def get_hi_byte(self):
    if self.connected == True:
      gpio_cmd = "\x83"
      self.ftdi.bulkWrite(self.OUT_EP, gpio_cmd)
      return self.ftdi.bulkRead(self.IN_EP, 1, timeout=500)
          
  def set_lo_byte(self, value, direction):
    if self.connected == True:
      gpio_cmd = struct.pack("BBB", 0x80, value, direction)
      self.ftdi.bulkWrite(self.OUT_EP, gpio_cmd)

  def set_hi_byte(self, value, direction):
    if self.connected == True:
      gpio_cmd = struct.pack("BBB", 0x82, value, direction)
      self.ftdi.bulkWrite(self.OUT_EP, gpio_cmd)
      
  def write_data(self, data):
    """
    Write data to be clocked out on falling clock edge (SPI like)
    """
    if self.connected == True:
      self.init_pins()
      data_len = len(data)
      cmd_data = struct.pack("<BH%ds" % data_len, 0x11, data_len-1, data) 
      self.ftdi.bulkWrite(self.OUT_EP, cmd_data)

  def load_image(self, file):
    """
    Loads a image and scale it in preparation of displaying
    """
    try:
      im = Image.open(file)
      orig_width, orig_height = im.size
      scale = orig_height / self.max_height
      width = orig_width / scale
      height = self.max_height
      im = im.resize((width, height)) 
      im = im.convert("RGB") #Convert to RGB image to be sure

      pix = im.load()
      self.image_data = []
      #For each column
      for x in range(width):
        column = []
        #Get the RGB values for the column and map them to the strings
        for y in reversed(range(height)):
          #The reversed y range is to have the controller at the bottom so
          #the bottom pixel will have to be sent out first.
          column.append( struct.pack( "BBB", pix[x,y][self.r_idx],
                                             pix[x,y][self.g_idx], 
                                             pix[x,y][self.b_idx] ) )
        self.image_data.append( "".join(column) )
    
      self.image_loaded = True
      self.update_status("Image loaded with %i columns" % width)
    except Exception,e:
      self.update_status("Error : %s" % e)
  
  def set_interval(self, interval):
    self.column_interval = interval
    self.update_status("Info: Interval set to %f s" % interval)
    
  def play_image(self):
    if self.image_loaded == True:
      self.update_status("Info: Playing image")
      for column in self.image_data:
        self.write_data(column)
        time.sleep(self.column_interval)
      self.write_data(self.black_column)
    else:
      self.update_status("Error: No image loaded")
  
  def set_colour(self, red, green, blue, pixel_index):
    colour = [red, green, blue]
    pixel = struct.pack( "BBB", colour[self.r_idx],
                                colour[self.g_idx], 
                                colour[self.b_idx] )
    if (pixel_index >=0 and pixel_index <self.max_height):
      self.column_buffer[pixel_index] = pixel
      self.update_status("Info: Showing (%d,%d,%d) on pixel %d" % (red, green, blue, pixel_index))
    else:
      self.column_buffer = self.max_height * [pixel]
      self.update_status("Info: Showing (%d,%d,%d)" % (red, green, blue))
    
    self.write_data("".join(self.column_buffer))
    
  def update_status(self, text):
    self.status = text
    self.status_updated.acquire()
    self.status_id += 1
    self.status_updated.notifyAll()
    self.status_updated.release()
  
  def get_status(self):
    status = {}
    status["status"]            = self.status
    status["status_id"]         = self.status_id
    return status

class BBBHTTPServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
  """
  BitBltBlade HTTP Server
  """
  daemon_threads = True
  
  def __init__(self, server_address, RequestHandlerClass):
    SocketServer.TCPServer.__init__(self, server_address, RequestHandlerClass)
    self.bbb_control = BBBControl()
    
class HTTPHandler(BaseHTTPServer.BaseHTTPRequestHandler):
  """
  BitBltBlade HTTP Handler
  This handles the communications with the HTML front end and interprets
  the post commands.
  """
  def __init__(self, request, client_address, server):

    BaseHTTPServer.BaseHTTPRequestHandler.__init__(self, request, client_address, server)
    
  def do_GET(self):
    if self.path == "/":
      try:
        f = open("bbbcontrol.html", "r")
        response = f.read()
        self.send_response(200)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)
        f.close()
      except:
        self.send_error(404, "Banana Not Found.")
        self.end_headers()
    elif self.path == "/icon/":
      try:
        f = open("bbbcontrol.png", "r")
        response = f.read()
        self.send_response(200)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)
        f.close()
      except:
        self.send_error(404, "Banana Not Found.")
        self.end_headers()
    elif self.path[:8] == "/status/":
      try:
        request_id = int(self.path[8:])
      except:
        request_id = 0
      self.server.bbb_control.status_updated.acquire()
      while request_id == self.server.bbb_control.status_id:
        self.server.bbb_control.status_updated.wait()      
      self.server.bbb_control.status_updated.release()
      
      response = "Status = " + json.dumps(self.server.bbb_control.get_status())
      self.send_response(200)
      self.send_header("Content-Length", str(len(response)))
      self.end_headers()
      self.wfile.write(response)
  def do_POST(self):
    if int(self.headers["content-length"]) > 1048576:
      #Request too big for poor router
      self.send_error(413)
      self.end_headers()
      self.server.bbb_control.update_status("Error: File too big")
      return
    if self.path == "/postdata/":
      form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={ 'REQUEST_METHOD':'POST',
                          'CONTENT_TYPE':self.headers['Content-Type']
                        }    )
      
      if form.has_key("imagefile"):
        self.server.bbb_control.load_image(form["imagefile"].file)
      elif form.has_key("interval"):
        #Interval indicates LS mode already
        interval = float(form["interval"].value)
        self.server.bbb_control.set_interval( interval )
      elif form.has_key("play"):
        #Indicates LS mode already
        self.server.bbb_control.play_image()
      elif ( form.has_key("blind") and 
             form.has_key("unit") and 
             form.has_key("command") ):
        blind=int(form["blind"].value)
        command=int(form["command"].value)
        unit_id=int(form["unit"].value)
        self.server.bbb_control.somfy_command(unit_id, blind, command)
      elif ( form.has_key("red") and 
             form.has_key("green") and
             form.has_key("blue") and
             form.has_key("pixel") ):
        red=int(form["red"].value)
        green=int(form["green"].value)
        blue=int(form["blue"].value)
        pixel=int(form["pixel"].value)
        self.server.bbb_control.set_colour(red,green,blue,pixel)
      else:
        #Unexpected case, send error back and update status
        self.send_error(405)
        self.end_headers()
        self.server.bbb_control.update_status("Error: Unexpected command")
        return
        
      self.send_response(200)
      self.end_headers()
    else:
      #Post operation on unsupported path
      self.send_error(405)
      self.end_headers()

  do_HEAD = do_GET
  
if __name__ == '__main__':    
  print "Starting BBB Control"
  #Prepare the HTTP Server
  try:
    http_server =  BBBHTTPServer(("",80),HTTPHandler)
    print "Started on Port 80"
  except:
    http_server =  BBBHTTPServer(("",8080),HTTPHandler)
    print "Started on Port 8080"
  httpd_thread = threading.Thread(target=http_server.serve_forever)
  httpd_thread.setDaemon(True)  
  httpd_thread.start()

  try:
    while True:
      httpd_thread.join(60)
  except KeyboardInterrupt:
    #Exit
    http_server.shutdown()

