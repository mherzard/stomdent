#!/bin/zsh
# Запуск бота STOM в фоновом режиме

cd "$(dirname "$0")"

# Проверка виртуального окружения
if [ ! -d "venv" ]; then
    echo "Создаю виртуальное окружение..."
    python3 -m venv venv
fi

source venv/bin/activate

# Установка/обновление зависимостей
pip install -q -r requirements.txt

# Проверка PID-файла
if [ -f bot.pid ]; then
    OLD_PID=$(cat bot.pid)
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "❌ Бот уже запущен (PID: $OLD_PID)"
        echo "   Для остановки выполните: ./stop_bot.sh"
        exit 0
    else
        rm bot.pid
    fi
fi

# Создание логов
mkdir -p logs

echo "🚀 Запуск бота STOM..."
nohup python bot.py > logs/bot.log 2>&1 &
PID=$!
echo $PID > bot.pid

sleep 2
if ps -p "$PID" > /dev/null 2>&1; then
    echo "✅ Бот запущен (PID: $PID)"
    echo "   Логи: logs/bot.log"
    echo "   Остановка: ./stop_bot.sh"
else
    echo "❌ Ошибка запуска. Проверьте логи:"
    tail -n 20 logs/bot.log
    rm -f bot.pid
fi
