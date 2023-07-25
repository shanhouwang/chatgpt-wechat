import re

def remove_value(string):
    pattern = r'wechat_tmp/\d+-\d+\.png'
    result = re.sub(pattern, '', string)
    return result

# 调用示例
test_string = "xxx:as;dfas:wechat_tmp/230825-175857.png"
result_string = remove_value(test_string)
print(result_string)