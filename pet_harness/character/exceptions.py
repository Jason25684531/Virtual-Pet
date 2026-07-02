class InvalidCharacterIdError(ValueError):
    """character_id 格式不合法時拋出。"""
    pass


class CharacterAlreadyExistsError(Exception):
    """建立角色時 character_id 已存在。"""
    pass


class CharacterNotFoundError(Exception):
    """操作的 character_id 不存在。"""
    pass
