#include "FireColumnModel.hpp"
#include <iostream>

int main() {
    FireColumnModel model;
    std::cout << "Model created with " << model.measurementCount() << " measurements\n";
    return 0;
}

