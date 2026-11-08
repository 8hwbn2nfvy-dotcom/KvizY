# KvizY

Квизы по своим лекциям, конспектам и слайдам — для самообучения. Загрузил файлы → ИИ сделал квиз → проходишь карточками → видишь слабые темы и рост.

- Как устроено и что где менять: [docs/karkas.md](docs/karkas.md)
- Запуск в Yandex Cloud по шагам: [docs/yandex_cloud.md](docs/yandex_cloud.md)
- Ошибки за проект и выводы: [docs/oshibki.md](docs/oshibki.md)

Запуск на компьютере (с пробным ИИ, без денег):
```
pip install -r requirements-dev.txt
KVIZY_PASSWORD=test SESSION_SECRET=x KEY_SECRET=y AI_PROVIDER=mock uvicorn app.main:app --reload
python -m pytest -q tests
```
