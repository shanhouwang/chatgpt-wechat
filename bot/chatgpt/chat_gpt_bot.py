# encoding:utf-8
import json
import os
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
from common.log import logger
from common.token_bucket import TokenBucket
from config import conf, load_config
import fcntl
import time
from lib import itchat
import re


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

    def reply(self, query, context=None):
        # acquire reply content
        if context.type == ContextType.TEXT:
            logger.info("[CHATGPT] query={}".format(query))

            session_id = context["session_id"]
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
            try:
                use_simple_change = self.is_valid_format(query)
                if use_simple_change:
                    url = "http://192.168.0.104:8080/mj/submit/simple-change"
                    headers = {"Content-Type": "application/json", "Accept": "application/json"}
                    response = requests.request("POST", url, headers=headers,
                                                data=json.dumps({"content": query.upper()}))
                    logger.info(f"[simple-change] query: {query}, {response.json()} ")
                    code = response.json()["code"]
                    result_id = response.json()["result"]
                else:
                    url = "http://192.168.0.104:8080/mj/submit/imagine"
                    headers = {"Content-Type": "application/json", "Accept": "application/json"}
                    response = requests.request("POST", url, headers=headers, data=json.dumps({"prompt": query}))
                    logger.info(f"[imagine] query: {query}, {response} ")
                    code = response.json()["code"]
                    result_id = response.json()["result"]
                if code == 1:
                    progress_init_tip_once = True
                    progress_0_20_once = True
                    progress_30_50_once = True
                    progress_60_80_once = True
                    progress_90_100_once = True
                    start_time = time.time()
                    while True:
                        file_name = f"/Users/shawn/PycharmProjects/chatgpt-on-wechat/channel/mj_notify_data_{result_id}.txt"
                        logger.info(file_name)
                        # 进行需要轮询的操作
                        if os.path.exists(file_name):
                            with open(file_name, "r+") as file:
                                try:
                                    fcntl.flock(file, fcntl.LOCK_EX)  # 获取排它锁
                                    # 在这里进行读取或写入文件的操作
                                    file_contents = file.read()
                                    json_data = json.loads(file_contents)
                                    mj_success = json_data["status"] == "SUCCESS"
                                    mj_image_url = json_data["imageUrl"]
                                    # 提示消息
                                    progress_json = json_data.get("progress", "0%")
                                    try:
                                        progress_value = int(progress_json.strip("%"))
                                    except (ValueError, TypeError):
                                        progress_value = 0  # 在出现异常情况下设定一个默认值
                                    if 0 < progress_value < 20 and progress_0_20_once:
                                        progress_tip = "正在画：" + json_data["prompt"][0:10] + f"({result_id})..." + json_data["progress"]
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_0_20_once = False
                                    elif 40 < progress_value < 50 and progress_30_50_once:
                                        progress_tip = "正在画：" + json_data["prompt"][0:10] + f"({result_id})..." + json_data["progress"]
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_30_50_once = False
                                    elif 60 < progress_value < 80 and progress_60_80_once:
                                        progress_tip = "正在画：" + json_data["prompt"][0:10] + f"({result_id})..." + json_data["progress"]
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_60_80_once = False
                                    elif 90 < progress_value < 100 and progress_90_100_once:
                                        progress_tip = "正在画：" + json_data["prompt"][0:10] + f"({result_id})..." + json_data["progress"]
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_90_100_once = False
                                    elif progress_value == 0 and progress_init_tip_once:
                                        progress_tip = "正在画：" + json_data["prompt"][0:10] + f"({result_id})..." + json_data["progress"]
                                        itchat.send_msg(progress_tip, toUserName=context["receiver"])
                                        progress_init_tip_once = False
                                finally:
                                    fcntl.flock(file, fcntl.LOCK_UN)  # 释放锁

                        if mj_success:
                            os.remove(file_name)
                            break  # 跳出轮询循环

                        # 检查是否超过1分钟
                        elapsed_time = time.time() - start_time
                        if elapsed_time > 60*5:
                            print("轮询超时，停止轮询")
                            break
                        time.sleep(1)
                elif code == 21:
                    mj_success = True
                    mj_image_url = response.json()["properties"]["imageUrl"]
            except FileNotFoundError:
                logger.info(
                    "File mj_notify_data not found. Please check the file path or create the file if it doesn't exist.")
            except IOError as e:
                logger.info(f"An IOError occurred while reading the file: {e}")
            except Exception as e:
                logger.info(f"An Exception: {e}")

            if mj_success:
                ok = mj_success
                ret_string = mj_image_url
            else:
                ok, ret_string = self.create_img(query, 0)
            reply = None
            if ok:
                reply = Reply(ReplyType.IMAGE_URL, ret_string)
            else:
                reply = Reply(ReplyType.ERROR, ret_string)
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
