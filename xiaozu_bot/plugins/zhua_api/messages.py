inner_group_id = 1020661785
outer_group_id = 1017615898
#inner_group_id = 730238212
#outer_group_id = 870217476
def json_check(id: int,num: int) -> set:
    return {
        'group_id': inner_group_id,
        'message': [{
            'type': 'text',
            'data': {
                'text': f'.berry_check * {id} {num}'
            }
        }]}
def json_change(id: int,num: int) -> set:
    return {
        'group_id': inner_group_id,
        'message': [{
            'type': 'text',
            'data': {
                'text': f'.berry_change * {id} {num}'
                    }
        }]}
def json_group(group_id: int, text: str) -> set:
    return {
        'group_id': group_id,
        'message': [{
            'type': 'text',
            'data': {
                'text': text
                    }
        }]}
def json_private(user_id: int, text: str) -> set:
    return {
        'user_id': user_id,
        'message': [{
            'type': 'text',
            'data': {
                'text': text
                    }
        }]}
def json_group_at(group_id: int, id: int, text: str) -> set:
    return {
        'group_id': group_id,
        'message': [
        {
            "type": "at",
            "data": {
                "qq": id
            }
        },{
            'type': 'text',
            'data': {
                'text': text
                    }
        }]}
def json_emoji_like(id: int,emoji_id: str) -> set:
    return {
    "message_id": id,
    "emoji_id": emoji_id
}