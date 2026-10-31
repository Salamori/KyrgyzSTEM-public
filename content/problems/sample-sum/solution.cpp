#include <iostream>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int n;
    std::cin >> n;
    long long total = 0;
    for (int i = 0; i < n; ++i) {
        long long value;
        std::cin >> value;
        total += value;
    }
    std::cout << total << '\n';
    return 0;
}
