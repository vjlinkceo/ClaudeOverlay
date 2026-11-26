import unittest

from overlay.speech.transcript import Transcript, Utterance, detect_question, is_echo


class QuestionTests(unittest.TestCase):
    def test_question_mark(self):
        self.assertEqual(detect_question("Ну хорошо. Чем процесс отличается от потока?"), "strong")

    def test_question_in_the_middle_of_text(self):
        text = "Мы делаем бэкенд на Django. А скажите, как работает GIL. Ну и про асинхронность."
        self.assertEqual(detect_question(text), "weak")

    def test_requests(self):
        self.assertEqual(detect_question("Расскажите про индексы в PostgreSQL"), "weak")
        self.assertEqual(detect_question("Поясніть, будь ласка, різницю між процесом і потоком"), "weak")
        self.assertEqual(detect_question("Okay, walk me through your last project"), "weak")

    def test_statements(self):
        self.assertIsNone(detect_question("Сегодня мы поговорим о базах данных."))
        self.assertIsNone(detect_question("Каково это — неважно."))

    def test_only_last_sentences(self):
        text = "Что такое GIL? Сейчас объясню. Это глобальная блокировка. Она мешает потокам."
        self.assertEqual(detect_question(text), "strong")
        self.assertIsNone(detect_question(text, last=2))


class TranscriptTests(unittest.TestCase):
    def test_merge_same_speaker(self):
        tr = Transcript()
        tr.add(Utterance("system", "Добрый день.", 100.0, 101.0))
        tr.add(Utterance("system", "Начнём?", 101.5, 102.0))
        tr.add(Utterance("mic", "Да.", 102.5, 103.0))
        self.assertEqual([u.text for u in tr.items], ["Добрый день. Начнём?", "Да."])

    def test_prompt_labels_and_partial(self):
        tr = Transcript()
        tr.add(Utterance("system", "Как дела?", 100.0, 101.0))
        tr.set_partial("mic", "Нормально")
        text = tr.text_for_prompt(labeled=True, max_chars=10000)
        self.assertIn("Them: Как дела?", text)
        self.assertIn("Me (still speaking): Нормально", text)
        unlabeled = tr.text_for_prompt(labeled=False, max_chars=10000)
        self.assertNotIn("Them", unlabeled)
        self.assertIn("(still speaking): Нормально", unlabeled)

    def test_prompt_trimmed_from_the_start(self):
        tr = Transcript()
        for i in range(50):
            tr.add(Utterance("system", f"phrase number {i}", i * 10.0, i * 10.0 + 1))
        text = tr.text_for_prompt(labeled=False, max_chars=300)
        self.assertTrue(text.startswith("[…] (earlier transcript omitted)"))
        self.assertIn("phrase number 49", text)
        self.assertLessEqual(len(text), 400)

    def test_echo(self):
        system = Utterance("system", "Чем процесс отличается от потока", 100.0, 103.0)
        echo = Utterance("mic", "процесс отличается от потока", 100.2, 103.1)
        own = Utterance("mic", "Процесс имеет своё адресное пространство", 104.0, 107.0)
        self.assertTrue(is_echo(echo, system))
        self.assertFalse(is_echo(own, system))
        tr = Transcript()
        tr.add(system, echo_filter=True)
        self.assertIsNone(tr.add(echo, echo_filter=True))
        self.assertIsNotNone(tr.add(own, echo_filter=True))

    def test_late_system_removes_mic_echo(self):
        tr = Transcript()
        tr.add(Utterance("mic", "процесс отличается от потока", 100.2, 103.1), echo_filter=True)
        tr.add(Utterance("system", "Чем процесс отличается от потока", 100.0, 103.0), echo_filter=True)
        self.assertEqual([u.source for u in tr.items], ["system"])


if __name__ == "__main__":
    unittest.main()
