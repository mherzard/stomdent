#!/bin/zsh
# Остановка бота STOM

cd "$(dirname "$0")"

if [ -f bot.pid ]; then
    PID=$(cat bot.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        kill "$PID"
        echo "🛑 Бот остановлен (PID: $PID)"
    else
        echo "⚠️ Процесс не найден (PID: $PID)"
    fi
    rm -f bot.pid
else
    echo "ℹ️ Бот не запущен (нет PID-файла)"
    # Попробуем найти процесс по имени файла
    PIDS=$(pgrep -f "python.*bot.py")
    if [ -n "$PIDS" ]; then
        echo "Найдены процессы: $PIDS"
        echo "Для остановки всех: kill $PIDS"
    fi
fi
