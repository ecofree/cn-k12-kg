"""只读历史批次视图；唯一题源为 data/question_sources/application.json。"""
from ._declarative import items_for

ITEMS = items_for('h2c')
