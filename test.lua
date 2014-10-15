#! /usr/bin/lua

local count = 0

-- Header
print [[
Content-type: multipart/x-mixed-replace;boundary=www.madox.net

]]

-- Multipart Body
while true do
  print [[
--www.madox.net
Content-Type: application/javascript

]]
  print(count)
  count = count + 1
  print("\r\n")
end
