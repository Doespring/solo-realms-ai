# Источники

Проверки характеристик реализованы по открытой SRD 5.2.1 (2024 rules, стр. 6–8). Полные коммерческие книги не использованы и не включены.

This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

- KoboldCpp: https://github.com/LostRuins/koboldcpp/releases/tag/v1.122.1
- Основная модель: https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 (Apache-2.0); GGUF: https://huggingface.co/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF, revision `4edb920b6f14e3b9284d4502a6485103d72cde05`.
- Сравнительная модель: https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF, revision `7dabda4d13d513e3e842b20f0d435c732f172cbe`, Qwen Research License. Не выбрана основной и не нужна для запуска.
- whisper.cpp: https://github.com/ggml-org/whisper.cpp/releases/tag/b5454; многоязычная Base: https://huggingface.co/ggerganov/whisper.cpp, revision `5359861c739e955e79d9a303bcbc70fb988958b1`.

Ссылки загрузки и SHA256 загруженных GGUF/KoboldCpp хранятся рядом с локальными файлами в `*.source.json`. Файлы runtime и модели не включены в Git. При переносе или распространении учитывайте лицензии каждого компонента.

Оригинальный пользовательский регламент Мастера из 14 глав не предоставлен отдельным файлом. Он не изучен и не подменён кратким перечнем из ТЗ; его последующий импорт относится к подготовке документов.
