import re


class Utils:

    @staticmethod
    def remove_prefix_mj_sd(input_string):
        if input_string.startswith("$mj"):
            return input_string[len("$mj"):]
        elif input_string.startswith("$sd"):
            return input_string[len("$sd"):]
        else:
            return input_string

    @staticmethod
    def check_prefix_mj(input_string):
        return input_string.startswith("$mj")

    @staticmethod
    def check_prefix_sd(input_string):
        return input_string.startswith("$sd")

    @staticmethod
    def check_prefix_mj_u(input_string):
        return input_string.startswith("$mju") or input_string.startswith("$mj u")

    @staticmethod
    def check_prefix_mj_v(input_string):
        return input_string.startswith("$mjv") or input_string.startswith("$mj v")

    @staticmethod
    def check_prefix_mj_r(input_string):
        return input_string.startswith("$mjr") or input_string.startswith("$mj r")

    @staticmethod
    def extract_ref_msg_mj_task_id(input_string):
        pattern = r"任务ID: (\d+)"
        match = re.search(pattern, input_string)
        if match:
            task_id = match.group(1)
            return task_id
        return None

    # "mj v 3817106158309817"
    @staticmethod
    def extract_mj_task_id(string):
        pattern = r'[uvr]\s?(\d+)'
        match = re.search(pattern, string)
        if match:
            number = match.group(1)
            return number
        return None

    @staticmethod
    def extract_mj_u_v_index(string):
        pattern = r"[u|v](\d)"
        result = re.findall(pattern, string)
        if result:
            first_digit = result[0]
        else:
            first_digit = None
        return first_digit

    @staticmethod
    def extract_http_local_urls(input_string):
        # 定义图片链接的正则表达式
        image_urls_pattern = r"http[s]?://[^\s]+(?:jpg|jpeg|png|gif|bmp|svg)"

        # 定义图片链接的正则表达式
        pattern = r"wechat_tmp/[^\s]+(?:jpg|jpeg|png|gif|bmp|svg)"

        # 使用 re.findall() 查找所有匹配的图片链接
        image_http_urls = re.findall(image_urls_pattern, input_string, re.IGNORECASE)

        # 使用 re.findall() 查找所有匹配的图片链接和本地文件地址
        image_local_urls = re.findall(pattern, input_string, re.IGNORECASE)

        return image_http_urls, image_local_urls

    @staticmethod
    def is_just_desc_wechat_pic(input_string):
        pattern = r"^desc wechat_tmp/\d{6}-\d{6}\.png$"
        if re.match(pattern, input_string):
            return True
        else:
            return False
