# encoding:utf-8
import base64
import fcntl
import json
import os
import re
import threading
import time

import openai
import openai.error
import requests

from bot.bot import Bot
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from bot.openai.open_ai_image import OpenAIImage
from bot.session_manager import SessionManager
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel.common_utils import Utils
from common.log import logger
from common.token_bucket import TokenBucket
from config import conf, load_config
from lib import itchat


# OpenAI对话模型API (可用)
class ChatGPTBot(Bot, OpenAIImage):

    def __init__(self):
        super().__init__()
        # set the default api_key
        openai.api_key = conf().get("open_ai_api_key")
        if conf().get("open_ai_api_base"):
            openai.api_base = conf().get("open_ai_api_base")
        proxy = conf().get("proxy")
        if proxy:
            openai.proxy = proxy
        if conf().get("rate_limit_chatgpt"):
            self.tb4chatgpt = TokenBucket(conf().get("rate_limit_chatgpt", 20))

        self.sessions = SessionManager(ChatGPTSession, model=conf().get("model") or "gpt-3.5-turbo")
        self.args = {
            "model": conf().get("model") or "gpt-3.5-turbo",  # 对话模型的名称
            "temperature": conf().get("temperature", 0.9),  # 值在[0,1]之间，越大表示回复越具有不确定性
            # "max_tokens":4096,  # 回复最大的字符数
            "top_p": conf().get("top_p", 1),
            "frequency_penalty": conf().get("frequency_penalty", 0.0),  # [-2,2]之间，该值越大则更倾向于产生不同的内容
            "presence_penalty": conf().get("presence_penalty", 0.0),  # [-2,2]之间，该值越大则更倾向于产生不同的内容
            "request_timeout": conf().get("request_timeout", None),  # 请求超时时间，openai接口默认设置为600，对于难问题一般需要较长时间
            "timeout": conf().get("request_timeout", None),  # 重试超时时间，在这个时间内，将会自动重试
        }

    def get_http_file_base64(self, url):
        # 发送HTTP请求，下载文件内容
        response = requests.get(url)
        if response.status_code == 200:
            # 获取文件内容
            file_content = response.content
            # 将文件内容转换为Base64编码
            base64_data = base64.b64encode(file_content)
            base64_string = base64_data.decode('utf-8')

            return base64_string
        else:
            logger.info(f"无法下载文件，HTTP状态码：{response.status_code}")
            return None

    def get_local_wechat_pic_base64(self, wechat_pic_path):
        base64_wechat_pic = None
        try:
            file_path = f"/Users/shawn/PycharmProjects/chatgpt-on-wechat/{wechat_pic_path}"
            with open(file_path, "rb") as file:
                base64_wechat_pic = base64.b64encode(file.read()).decode("utf-8")
        except Exception as e:
            logger.info(f"An Exception: {e}")
        finally:
            return base64_wechat_pic

    def extract_http_local_urls(self, input_string):
        # 定义图片链接的正则表达式
        image_urls_pattern = r"http[s]?://[^\s]+(?:jpg|jpeg|png|gif|bmp|svg)"

        # 定义图片链接的正则表达式
        pattern = r"wechat_tmp/[^\s]+(?:jpg|jpeg|png|gif|bmp|svg)"

        # 使用 re.findall() 查找所有匹配的图片链接
        image_http_urls = re.findall(image_urls_pattern, input_string, re.IGNORECASE)

        # 使用 re.findall() 查找所有匹配的图片链接和本地文件地址
        image_local_urls = re.findall(pattern, input_string, re.IGNORECASE)

        return image_http_urls, image_local_urls

    def is_just_wechat_pic(self, input_string):
        pattern = r"^wechat_tmp/\d{6}-\d{6}\.png$"
        if re.match(pattern, input_string):
            return True
        else:
            return False

    def get_wechat_pic(self, input_string):
        pattern = r"(wechat_tmp/\d{6}-\d{6}\.png)"
        match = re.search(pattern, input_string)
        if match:
            matched_value = match.group(1)
            return matched_value
        else:
            return None

    def remove_wechat_pic_value(self, input_string):
        pattern = r'wechat_tmp/\d+-\d+\.png'
        result = re.sub(pattern, '', input_string)
        return result

    def is_valid_format(self, input_string):
        # 定义正则表达式匹配模式
        pattern = r'^\s*\d{16}\s+[UVuV][1-4]\s*$'
        # 使用re模块的match函数进行匹配
        if re.match(pattern, input_string):
            return True
        else:
            return False

    def extract_number(self, input_string):
        pattern = r'\b\d+\b'  # 匹配一个或多个数字，只匹配单词边界处的数字
        number = re.search(pattern, input_string)
        return number.group()

    def remove_spaces_and_backspaces(input_string):
        # 使用正则表达式替换空格和回撤字符
        cleaned_string = re.sub(r'[ \x08]', '', input_string)
        return cleaned_string

    def get_chatgpt_content(self, session_id, query, context):
        reply = None
        clear_memory_commands = conf().get("clear_memory_commands", ["#清除记忆"])
        if query in clear_memory_commands:
            self.sessions.clear_session(session_id)
            reply = Reply(ReplyType.INFO, "记忆已清除")
        elif query == "#清除所有":
            self.sessions.clear_all_session()
            reply = Reply(ReplyType.INFO, "所有人记忆已清除")
        elif query == "#更新配置":
            load_config()
            reply = Reply(ReplyType.INFO, "配置已更新")
        if reply:
            return reply
        session = self.sessions.session_query(query, session_id)
        logger.info("[CHATGPT] session query={}".format(session.messages))

        api_key = context.get("openai_api_key")
        model = context.get("gpt_model")
        new_args = None
        if model:
            new_args = self.args.copy()
            new_args["model"] = model
        # if context.get('stream'):
        # reply in stream
        # return self.reply_text_stream(query, new_query, session_id)
        reply_content = self.reply_text(session, api_key, args=new_args)
        logger.debug(
            "[CHATGPT] new_query={}, session_id={}, reply_cont={}, completion_tokens={}".format(
                session.messages,
                session_id,
                reply_content["content"],
                reply_content["completion_tokens"],
            )
        )
        return reply_content

    def reply(self, query, context=None):
        # acquire reply content
        if context.type == ContextType.TEXT:
            logger.info("[CHATGPT] query={}".format(query))
            session_id = context["session_id"]
            reply_content = self.get_chatgpt_content(session_id, query, context)
            if reply_content["completion_tokens"] == 0 and len(reply_content["content"]) > 0:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
            elif reply_content["completion_tokens"] > 0:
                self.sessions.session_reply(reply_content["content"], session_id, reply_content["total_tokens"])
                reply = Reply(ReplyType.TEXT, reply_content["content"])
            else:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
                logger.info("[CHATGPT] reply {} used 0 tokens.".format(reply_content))
            return reply

        elif context.type == ContextType.IMAGE_CREATE:
            mj_success = False
            mj_image_url = ""
            prompts_desc = None
            use_mj_prefix = Utils.check_prefix_mj(query)
            use_sd_prefix = Utils.check_prefix_sd(query)
            check_prefix_mj_u = Utils.check_prefix_mj_u(query)
            check_prefix_mj_v = Utils.check_prefix_mj_v(query)
            check_prefix_mj_r = Utils.check_prefix_mj_r(query)
            try:
                image_http_urls, image_local_urls = self.extract_http_local_urls(query)
                logger.info(f"{image_http_urls} {image_local_urls}")
                wechat_pic_path = self.get_wechat_pic(query)
                wechat_http_path = Utils.get_http_pic(query)
                use_simple_change = self.is_valid_format(query)
                base64_wechat_pic = None
                if Utils.is_just_desc_wechat_pic(query) or Utils.is_just_desc_http_pic(query):
                    if wechat_pic_path:
                        base64_wechat_pic = f"data:image/png;base64,{self.get_local_wechat_pic_base64(wechat_pic_path)}"
                    elif wechat_http_path:
                        base64_wechat_pic = f"data:image/png;base64,{self.get_http_file_base64(wechat_http_path)}"
                    url = "http://192.168.0.104:8080/mj/submit/describe"
                    headers = {"Content-Type": "application/json", "Accept": "application/json"}
                    response = requests.request("POST", url, headers=headers, data=json.dumps({"base64": base64_wechat_pic}))
                    logger.info(f"[describe]: {query}, {response.json()} ")
                    code = response.json()["code"]
                    result_id = response.json()["result"]
                    description = response.json()["description"]
                elif (len(image_http_urls) > 0 and len(image_local_urls) > 0) or len(image_http_urls) > 1 or len(
                        image_local_urls) > 1:
                    url = "http://192.168.0.104:8080/mj/submit/blend"
                    headers = {"Content-Type": "application/json", "Accept": "application/json"}
                    base64_array = []
                    for http_url in image_http_urls:
                        http_file_base64 = self.get_http_file_base64(http_url)
                        if http_file_base64:
                            base64_array.append(f"data:image/png;base64,{http_file_base64}")
                    for path in image_local_urls:
                        local_file_base64 = self.get_local_wechat_pic_base64(path)
                        if local_file_base64:
                            base64_array.append(f"data:image/png;base64,{self.get_local_wechat_pic_base64(path)}")
                    response = requests.request("POST", url, headers=headers,
                                                data=json.dumps({"base64Array": base64_array, "dimensions": "SQUARE"}))
                    logger.info(f"[blend]: {query}, {response.json()} ")
                    code = response.json()["code"]
                    result_id = response.json()["result"]
                    description = response.json()["description"]
                elif use_simple_change:
                    url = "http://192.168.0.104:8080/mj/submit/simple-change"
                    headers = {"Content-Type": "application/json", "Accept": "application/json"}
                    response = requests.request("POST", url, headers=headers,
                                                data=json.dumps({"content": query.upper()}))
                    logger.info(f"[simple-change] query: {query}, {response.json()} ")
                    code = response.json()["code"]
                    result_id = response.json()["result"]
                    description = response.json()["description"]
                elif (use_mj_prefix is True or use_sd_prefix is True) and (
                        check_prefix_mj_u or check_prefix_mj_v or check_prefix_mj_r):
                    url = "http://192.168.0.104:8080/mj/submit/change"
                    action = None
                    index = None
                    if check_prefix_mj_u:
                        action = "UPSCALE"
                        index = Utils.extract_mj_u_v_index(query)
                    elif check_prefix_mj_v:
                        action = "VARIATION"
                        index = Utils.extract_mj_u_v_index(query)
                    else:
                        action = "REROLL"
                    task_id = None
                    task_id = Utils.extract_ref_msg_mj_task_id(query)
                    if task_id is None:
                        task_id = Utils.extract_mj_task_id(query)
                    params = {"action": action}
                    if index is not None:
                        params["index"] = index
                    params["taskId"] = task_id
                    headers = {"Content-Type": "application/json", "Accept": "application/json"}
                    response = requests.request("POST", url, headers=headers, data=json.dumps(params))
                    logger.info(f"[change] query: {query}, params: {json.dumps(params)}, response: {response.json()} ")
                    code = response.json()["code"]
                    result_id = response.json()["result"]
                    description = response.json()["description"]
                else:
                    if wechat_pic_path:
                        base64_wechat_pic = f"data:image/png;base64,{self.get_local_wechat_pic_base64(wechat_pic_path)}"
                    elif wechat_http_path:
                        base64_wechat_pic = f"data:image/png;base64,{self.get_http_file_base64(wechat_http_path)}"
                    if use_mj_prefix is True or use_sd_prefix is True:
                        query = Utils.remove_prefix_mj_sd(query)
                    url = "http://192.168.0.104:8080/mj/submit/imagine"
                    headers = {"Content-Type": "application/json", "Accept": "application/json"}
                    if base64_wechat_pic:
                        query_new = self.remove_wechat_pic_value(query)
                    else:
                        query_new = query.rstrip()
                    if len(query_new) < 1:
                        return None
                    response = requests.request("POST", url, headers=headers,
                                                data=json.dumps({"base64": base64_wechat_pic, "prompt": query_new}))
                    logger.info(f"[imagine] query: {query_new}, {base64_wechat_pic is not None} {response.json()} ")
                    code = response.json()["code"]
                    result_id = response.json()["result"]
                    description = response.json()["description"]
                if code == 1:
                    progress_init_tip_once = True
                    progress_0_20_once = True
                    progress_30_50_once = True
                    progress_60_80_once = True
                    progress_90_100_once = True
                    start_time = time.time()
                    file_name = f"/Users/shawn/PycharmProjects/chatgpt-on-wechat/channel/mj_notify_data_{result_id}.txt"
                    while True:
                        logger.info(file_name)
                        # 进行需要轮询的操作
                        if os.path.exists(file_name):
                            with open(file_name, "r+") as file:
                                try:
                                    fcntl.flock(file, fcntl.LOCK_EX)  # 获取排它锁
                                    # 在这里进行读取或写入文件的操作
                                    file_contents = file.read()
                                    json_data = json.loads(file_contents)
                                    mj_success = json_data.get("status") == "SUCCESS"
                                    if mj_success and json_data.get("action") == "DESCRIBE":
                                        prompts_desc = json_data.get("prompt")
                                        itchat.send_msg(f"任务ID: {result_id} {prompt}... 进度100%",
                                                        toUserName=context["receiver"])
                                        itchat.send_msg(json_data.get("imageUrl"), toUserName=context["receiver"])
                                        os.remove(file_name)
                                        break
                                    mj_image_url = json_data.get("imageUrl")
                                    # 提示消息
                                    progress_json = json_data.get("progress", "0%")
                                    prompt = ""
                                    try:
                                        progress_value = int(progress_json.strip("%"))
                                        prompt = json_data.get("prompt")[0:10]
                                    except Exception as e:
                                        logger.info(f"A Exception: {e}")
                                        progress_value = 0  # 在出现异常情况下设定一个默认值
                                    if prompt:
                                        prompt = f"({prompt})"
                                    progress_tip = f"任务ID: {result_id} {prompt}... 进度{progress_json}"

                                    if 0 < progress_value < 20 and progress_0_20_once:
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_0_20_once = False
                                    elif 40 < progress_value < 50 and progress_30_50_once:
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_30_50_once = False
                                    elif 60 < progress_value < 80 and progress_60_80_once:
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_60_80_once = False
                                    elif 90 < progress_value <= 100 and progress_90_100_once:
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_90_100_once = False
                                    elif progress_value == 0 and progress_init_tip_once:
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_init_tip_once = False
                                finally:
                                    fcntl.flock(file, fcntl.LOCK_UN)  # 释放锁

                        if mj_success:
                            os.remove(file_name)
                            break  # 跳出轮询循环
                        # 检查是否超过1分钟
                        elapsed_time = time.time() - start_time
                        if elapsed_time > 60 * 3:
                            os.remove(file_name)
                            print("轮询超时，停止轮询")
                            break
                        time.sleep(1)
                elif code == 21:
                    mj_success = True
                    mj_image_url = response.json()["properties"]["imageUrl"]
            except IOError as e:
                logger.info(f"An IOError occurred while reading the file: {e}")
            except Exception as e:
                os.remove(file_name)
                itchat.send_msg(f"出现了异常: {e}", toUserName=context["receiver"])
                logger.info(f"An Exception: {e}")

            if prompts_desc:
                itchat.send_msg(prompts_desc, toUserName=context["receiver"])
                time.sleep(2)
                itchat.send_msg("正在翻译成中文", toUserName=context["receiver"])
                session_id = context["session_id"]
                reply_content = self.get_chatgpt_content(session_id, f"翻译以下内容：{prompts_desc}", context)
                if reply_content["completion_tokens"] == 0 and len(reply_content["content"]) > 0:
                    reply = prompts_desc
                elif reply_content["completion_tokens"] > 0:
                    reply = reply_content["content"]
                else:
                    reply = prompts_desc
                itchat.send_msg(reply, toUserName=context["receiver"])
                current_thread = threading.current_thread()
                thread_name = current_thread.name
                logger.info(f"当前线程的名字是：{thread_name}")
                return None
            elif mj_success:
                ok = mj_success
                ret_string = mj_image_url
                itchat.send_msg(ret_string, toUserName=context["receiver"])
            else:
                itchat.send_msg(f"任务ID: {result_id} 作图出现了异常：{description}", toUserName=context["receiver"])
            reply = None
            if ok:
                reply = Reply(ReplyType.IMAGE_URL, ret_string)
            return reply
        else:
            reply = Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))
            return reply

    def reply_text(self, session: ChatGPTSession, api_key=None, args=None, retry_count=0) -> dict:
        """
        call openai's ChatCompletion to get the answer
        :param session: a conversation session
        :param session_id: session id
        :param retry_count: retry count
        :return: {}
        """
        try:
            if conf().get("rate_limit_chatgpt") and not self.tb4chatgpt.get_token():
                raise openai.error.RateLimitError("RateLimitError: rate limit exceeded")
            # if api_key == None, the default openai.api_key will be used
            if args is None:
                args = self.args
            response = openai.ChatCompletion.create(api_key=api_key, messages=session.messages, **args)
            # logger.info("[CHATGPT] response={}".format(response))
            # logger.info("[ChatGPT] reply={}, total_tokens={}".format(response.choices[0]['message']['content'], response["usage"]["total_tokens"]))
            return {
                "total_tokens": response["usage"]["total_tokens"],
                "completion_tokens": response["usage"]["completion_tokens"],
                "content": response.choices[0]["message"]["content"],
            }
        except Exception as e:
            need_retry = retry_count < 2
            result = {"completion_tokens": 0, "content": "我现在有点累了，等会再来吧"}
            if isinstance(e, openai.error.RateLimitError):
                logger.warn("[CHATGPT] RateLimitError: {}".format(e))
                result["content"] = "提问太快啦，请休息一下再问我吧"
                if need_retry:
                    time.sleep(20)
            elif isinstance(e, openai.error.Timeout):
                logger.warn("[CHATGPT] Timeout: {}".format(e))
                result["content"] = "我没有收到你的消息"
                if need_retry:
                    time.sleep(5)
            elif isinstance(e, openai.error.APIError):
                logger.warn("[CHATGPT] Bad Gateway: {}".format(e))
                result["content"] = "请再问我一次"
                if need_retry:
                    time.sleep(10)
            elif isinstance(e, openai.error.APIConnectionError):
                logger.warn("[CHATGPT] APIConnectionError: {}".format(e))
                need_retry = False
                result["content"] = "我连接不到你的网络"
            else:
                logger.exception("[CHATGPT] Exception: {}".format(e))
                need_retry = False
                self.sessions.clear_session(session.session_id)

            if need_retry:
                logger.warn("[CHATGPT] 第{}次重试".format(retry_count + 1))
                return self.reply_text(session, api_key, args, retry_count + 1)
            else:
                return result


class AzureChatGPTBot(ChatGPTBot):
    def __init__(self):
        super().__init__()
        openai.api_type = "azure"
        openai.api_version = "2023-03-15-preview"
        self.args["deployment_id"] = conf().get("azure_deployment_id")

    def create_img(self, query, retry_count=0, api_key=None):
        api_version = "2022-08-03-preview"
        url = "{}dalle/text-to-image?api-version={}".format(openai.api_base, api_version)
        api_key = api_key or openai.api_key
        headers = {"api-key": api_key, "Content-Type": "application/json"}
        try:
            body = {"caption": query, "resolution": conf().get("image_create_size", "256x256")}
            submission = requests.post(url, headers=headers, json=body)
            operation_location = submission.headers["Operation-Location"]
            retry_after = submission.headers["Retry-after"]
            status = ""
            image_url = ""
            while status != "Succeeded":
                logger.info("waiting for image create..., " + status + ",retry after " + retry_after + " seconds")
                time.sleep(int(retry_after))
                response = requests.get(operation_location, headers=headers)
                status = response.json()["status"]
            image_url = response.json()["result"]["contentUrl"]
            return True, image_url
        except Exception as e:
            logger.error("create image error: {}".format(e))
            return False, "图片生成失败"
