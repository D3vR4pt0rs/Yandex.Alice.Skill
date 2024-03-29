import enum
import inspect
import sys
import csv
import math
import os
from abc import ABC, abstractmethod
from itertools import cycle
from random import shuffle
from typing import Optional

from modules.alice_library.alice import (
    GEOLOCATION_ALLOWED,
    GEOLOCATION_REJECTED,
)
from modules.log.log import logger

import alice_skill.constants as alice
from alice_skill.helper import check_time
from alice_skill.request import Request

with open("quiz.csv", "r", encoding="windows-1251") as csvfile:
    data = csv.DictReader(csvfile, delimiter=";", quotechar=" ")
    events = {x["question"]: [x["right_answer"], [x["wrong_answer1"], x["wrong_answer2"]], x["type"]] for x in data}


class Scene(ABC):

    @classmethod
    def id(cls):
        return cls.__name__

    """Генерация ответа сцены"""

    @abstractmethod
    def reply(self, request):
        raise NotImplementedError()

    """Проверка перехода к новой сцене"""

    def move(self, request: Request):
        next_scene = self.handle_local_intents(request)
        if next_scene is None:
            next_scene = self.handle_global_intents(request)
        return next_scene

    @abstractmethod
    def handle_global_intents(self):
        raise NotImplementedError()

    @abstractmethod
    def handle_local_intents(request: Request) -> Optional[str]:
        raise NotImplementedError()

    def fallback(self):
        return self.make_response('Извините, я Вас не поняла. Пожалуйста, попробуйте переформулировать вопрос.')

    def make_response(self, text, tts=None, card=None, state=None, buttons=None, directives=None):
        response = {
            'text': text,
            'tts': tts if tts is not None else text,
        }
        if card is not None:
            response['card'] = card
        if buttons is not None:
            response['buttons'] = buttons
        if directives is not None:
            response['directives'] = directives
        webhook_response = {
            'response': response,
            'version': '1.0',
            alice.STATE_RESPONSE_KEY: {
                'scene': self.id(),
            },
        }
        if state is not None:
            webhook_response[alice.STATE_RESPONSE_KEY].update(state)
        return webhook_response


class BarTourScene(Scene):

    def handle_global_intents(self, request):
        if alice.START_TOUR in request.intents:
            return StartQuest()
        elif alice.START_ACTIVITY in request.intents:
            return move_to_activity_scene(request, alice.START_ACTIVITY)
        elif alice.FIND_NEAR_PLACE in request.intents:
            return move_to_place_scene(request)


class Welcome(BarTourScene):
    def reply(self, request: Request):
        text = ('Добро пожаловать в Барские приключения.'
                'Я могу порекомендовать Вам хорошее месте, чтобы провести время или немного развлечь викториной.'
                'Для работы мне трубуется доступ к геолокации, даете разрешение?')
        directives = {'request_geolocation': {}}
        return self.make_response(text, buttons=[
            alice.ALICE.create_button('Начни экскурсию', hide=True),  # все хуйня, TODO надо понять, потому что требует расскажи экускурсию, а по логике это запрос геолокации
        ], directives=directives)

    def handle_local_intents(self, request: Request):
        logger.info('request type: ' + request.type)
        if request.type in (
                GEOLOCATION_ALLOWED,
                GEOLOCATION_REJECTED,
        ):
            return HandleGeolocation()


class StartQuest(BarTourScene):
    def reply(self, request: Request):
        alice.SESSION_STORAGE.pop(request.user_id, None)
        text = ''
        return self.make_response(text, state={
            'screen': 'start_tour'
        }, buttons=[
            alice.ALICE.create_button('Викторина'),
            alice.ALICE.create_button('Советник')
        ])

    def handle_local_intents(self, request: Request):
        if alice.START_ACTIVITY:
            return move_to_activity_scene(request, alice.START_ACTIVITY)


class HandleGeolocation(BarTourScene):
    def reply(self, request: Request):
        if request.type == GEOLOCATION_ALLOWED:
            location = request['session']['location']
            lat = location['lat']
            lon = location['lon']
            text = f'Ваши координаты: широта {lat}, долгота {lon}'
            return self.make_response(text)
        else:
            text = ('К сожалению, мне не удалось получить ваши координаты.'
                    'Поэтому я не могу вам советовать и провести квест, но можем поиграть в викторину')
            return self.make_response(text, directives={'request_geolocation': {}})

    def handle_local_intents(self, request: Request):
        pass


class Activity(enum.Enum):
    UNKNOWN = 1
    NOT_ALLOWED = 2
    QUEST = 3
    QUIZ = 4
    ADVICE = 5

    @classmethod
    def from_request(cls, request: Request, intent_name: str):
        slot = request.intents[intent_name]['slots']['place']['value']
        if slot == 'quest':
            if alice.ALICE.check_location(request):
                return cls.QUEST
            else:
                return cls.NOT_ALLOWED
        if slot == 'advice':
            if alice.ALICE.check_location(request):
                return cls.ADVICE
            else:
                return cls.NOT_ALLOWED
        elif slot == 'quiz':
            return cls.QUIZ
        else:
            return cls.UNKNOWN


def move_to_activity_scene(request: Request, intent_name: str):
    activity = Activity.from_request(request, intent_name)
    if activity == Activity.QUIZ:
        return Quiz()
    elif activity == Activity.QUEST:
        return Quest()
    elif activity == Activity.ADVICE:
        return Advice()
    elif activity == Activity.UNKNOWN:
        return Unknown()
    elif activity == Activity.NOT_ALLOWED:
        return NotAllowed()


class Quest(BarTourScene):
    def reply(self, request: Request):
        return self.make_response(text='Квестовик пьян, заходи в следующий раз.')

    def handle_local_intents(self, request: Request):
        pass


class Quiz(BarTourScene):
    def _choose_theme(self):
        text = 'Выбери тематику викторины'
        buttons = [
            alice.ALICE.create_button(title='История', hide=True),
            alice.ALICE.create_button(title='Места', hide=True),
            alice.ALICE.create_button(title='Коктейли', hide=True),
            alice.ALICE.create_button(title='Сервировка', hide=True)
        ]
        return text, buttons

    def _create_buttons(self, event, right_answer):
        buttons = [alice.ALICE.create_button(title=right_answer, hide=True)]
        for title in events[event][1]:
            buttons.append(alice.ALICE.create_button(title=title, hide=True))
        buttons.append(alice.ALICE.create_button(title="Выбрать тематику", hide=True))
        return buttons

    def _create_new_question(self, request: Request, ):
        event = next(alice.SESSION_STORAGE[request.user_id]["questions"])
        right_answer = events[event][0]
        buttons = self._create_buttons(event, right_answer)
        return event, right_answer, buttons

    def reply(self, request: Request):
        if request.user_id not in alice.SESSION_STORAGE:
            alice.SESSION_STORAGE[request.user_id] = {}
            text, buttons = self._choose_theme()
            return self.make_response(state={'screen': 'quiz'}, text=text, buttons=buttons)
        else:
            if request.command in ['история', "места", "коктейли", "сервировка"]:
                alice.SESSION_STORAGE[request.user_id].update(type=request.command)
                _a = list(filter(lambda x: request.command == events[x][2], events.keys()))
                shuffle(_a)
                inf_list = cycle(_a)
                alice.SESSION_STORAGE[request.user_id].update(questions=inf_list)

                event, right_answer, buttons = self._create_new_question(request)

                alice.SESSION_STORAGE[request.user_id].update(event=event)
                alice.SESSION_STORAGE[request.user_id].update(answer=right_answer)

                return self.make_response(state={'screen': 'quiz'}, text=event, buttons=buttons)
            elif request.command == alice.SESSION_STORAGE[request.user_id]["answer"]:
                event, right_answer, buttons = self._create_new_question(request)
                alice.SESSION_STORAGE[request.user_id].update(event=event)
                alice.SESSION_STORAGE[request.user_id].update(answer=right_answer)
                text = ('Верно!\n' f'{event}')
                return self.make_response(state={'screen': 'quiz'}, text=text, buttons=buttons)
            elif request.command == 'выбрать тематику':
                text, buttons = self._choose_theme()
                return self.make_response(state={'screen': 'quiz'}, text=text, buttons=buttons)
            else:
                buttons = self._create_buttons(alice.SESSION_STORAGE[request.user_id]["event"],
                                               alice.SESSION_STORAGE[request.user_id]["answer"])
                return self.make_response(state={'screen': 'quiz'}, text=("Неверно! Попробуй еще раз."),
                                          buttons=buttons)

    def handle_local_intents(self, request: Request):
        if alice.STOP_ACTIVITY:
            return StartQuest()


class Advice(BarTourScene):
    def reply(self, request: Request):
        return Place.place_from_geolocation(self, request)

    def handle_local_intents(self, request: Request):
        pass


class Unknown(BarTourScene):
    def reply(self, request: Request):
        return self.make_response(text='Извини друг, я понимаю что ты хочешь.')

    def handle_local_intents(self, request: Request):
        pass


class NotAllowed(BarTourScene):
    def reply(self, request: Request):
        return self.make_response(text='Прости, но пока Большой брат не следит за тобой, я не могу помочь.')

    def handle_local_intents(self, request: Request):
        pass


class Place(enum.Enum):
    ENCHANTRESS = 1
    ZAVOD_BAR = 2
    JAZZ_BLUES = 3
    GOAT = 4

    def _distance(cls, client_location={}, bar_location={}):
        return math.sqrt(
            pow((bar_location['lat'] - client_location['lat']), 2) + pow((bar_location['lon'] - client_location['lon']),
                                                                         2))

    @classmethod
    def place_from_geolocation(cls, request: Request):
        location_full = request['session']['location']
        location = {location_full['lat'], location_full['lon']}
        # location = {'lat': 58.521698, 'lon': 31.268701}

        distances = {}
        distances.update(enchantress=cls._distance(location, alice.ENCHANTRESS_location))
        distances.update(zavod_bar=cls._distance(location, alice.ZAVOD_BAR_location))
        distances.update(jazz_blues=cls._distance(location, alice.JAZZ_BLUES_location))
        distances.update(goat=cls._distance(location, alice.GOAT_location))

        # logger
        logger.info(distances)

        key_list = list(distances.keys())
        val_list = list(distances.values())
        min_distance = min(val_list)
        bar_name = key_list[val_list.index(min_distance)]

        if bar_name == "enchantress":
            return cls.ENCHANTRESS
        elif bar_name == "zavod_bar":
            return cls.ZAVOD_BAR
        elif bar_name == "jazz_blues":
            return cls.JAZZ_BLUES
        elif bar_name == "goat":
            return cls.GOAT


def move_to_place_scene(request: Request):
    place = Place.place_from_geolocation(request)
    if place == Place.ZAVOD_BAR:
        return Zavod()
    elif place == Place.GOAT:
        return Goat()
    elif place == Place.ENCHANTRESS:
        return Enchantress()
    elif place == Place.JAZZ_BLUES:
        return Jazz_blues()


class Zavod(BarTourScene):  # TODO
    def reply(self, request: Request):
        tts = ('"Завод бар" располагается на территории завода Алкон,'
               'его отличает от других баров русская направленность в кухне и напитках.'
               'А ещё там есть лавка с продукцией Алкона.'
               )  # TODO короткое описание завода
        return self.make_response(
            text='',
            tts=tts,
            card=alice.create_image_gallery(image_ids=[
                '213044/6b28d20a9faa88496151'
            ])
        )

    def handle_local_intents(self, request: Request):
        pass


class Enchantress(BarTourScene):  # TODO
    def reply(self, request: Request):
        tts = ('"Чародейка" - это одно из самых уникальных мест в Великом Новгороде.'
               'Это второе заведение, помимо "Zavod" бара, которое связано с главным местным производителем алкоголя Алконом.'
               'Бар отличается от других своими напитками и атмосферой.'
               )  # TODO короткое описание чародейки
        return self.make_response(
            text='',
            tts=tts,
            card=alice.create_image_gallery(image_ids=[
                '213044/6b28d20a9faa88496151'
            ])
        )

    def handle_local_intents(self, request: Request):
        pass


class Jazz_blues(BarTourScene):  # TODO
    def reply(self, request: Request):
        tts = ('Jazz&Blues был бы самым обычным баром, если бы не музыка.'
               'В этом баре Вы найдете уютную атмосферу, хорошую музыку или даже попадете на концерт.'
               )  # TODO короткое описание J&B
        return self.make_response(
            text='',
            tts=tts,
            card=alice.create_image_gallery(image_ids=[
                '213044/6b28d20a9faa88496151'
            ])
        )

    def handle_local_intents(self, request: Request):
        pass


class Goat(BarTourScene):  # TODO
    def reply(self, request: Request):
        tts = ('"Нафига козе баян" - это отличное место, чтобы провести вечер в хорошей компании.'
               'Тут Вас встретит отличная еда'
               )  # TODO короткое описание Нафига козе баян
        return self.make_response(
            text='',
            tts=tts,
            card=alice.create_image_gallery(image_ids=[
                '213044/6b28d20a9faa88496151'
            ])
        )

    def handle_local_intents(self, request: Request):
        pass


def _list_scenes():
    current_module = sys.modules[__name__]
    scenes = []
    for name, obj in inspect.getmembers(current_module):
        if inspect.isclass(obj) and issubclass(obj, Scene):
            scenes.append(obj)
    return scenes


SCENES = {
    scene.id(): scene for scene in _list_scenes()
}

DEFAULT_SCENE = Welcome
