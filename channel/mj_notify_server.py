from http.server import BaseHTTPRequestHandler, HTTPServer
import cgi
import json
import fcntl


# 创建一个简单的HTTP请求处理类
class MJNotifyServer(BaseHTTPRequestHandler):

    # 线程安全的写入文件操作
    @staticmethod
    def write_to_file(filename, data):
        with open(filename, "w") as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX)  # 获取排它锁
                # 在这里进行读取或写入文件的操作
                file.write(data)
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)  # 释放锁

    # 线程安全的读取文件操作
    @staticmethod
    def read_from_file(filename):
        with open(filename, "r+") as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX)  # 获取排它锁

                # 在这里进行读取或写入文件的操作
                file_contents = file.read()
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)  # 释放锁

    def do_GET(self):
        if self.path == '/notify':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'Hello, this is the notify GET endpoint!')
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'404 Not Found')

    def do_POST(self):
        if self.path == '/notify':
            content_type, _ = cgi.parse_header(self.headers.get('Content-Type'))
            if content_type == 'application/json':
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length).decode('utf-8')
                try:
                    json_data = json.loads(post_data)
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    response_data = {'message': 'JSON data received', 'data': json_data}
                    self.wfile.write(json.dumps(response_data).encode('utf-8'))
                    print(post_data)
                    MJNotifyServer.write_to_file(f"mj_notify_data_{json_data['id']}.txt", post_data)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b'Bad Request: Invalid JSON data')
            else:
                self.send_response(415)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b'Unsupported Media Type: application/json required')
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'404 Not Found')


# 定义服务器地址和端口
server_address = ('192.168.0.104', 4120)

# 创建HTTPServer实例并指定请求处理类
httpd = HTTPServer(server_address, MJNotifyServer)

# 开始监听请求，直到手动中断程序（Ctrl+C）
print(f"Starting server at {server_address[0]}:{server_address[1]}")

try:
    httpd.serve_forever()
except KeyboardInterrupt:
    pass

# 关闭服务器
httpd.server_close()
print("Server stopped.")
