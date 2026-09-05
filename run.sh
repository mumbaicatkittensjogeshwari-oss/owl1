#!/bin/bash
echo "Installing dependencies..."
pip install -r requirements_api.txt

echo "Starting server..."
python start.py